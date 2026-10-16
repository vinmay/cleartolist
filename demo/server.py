"""Hosted ClearToList (Render free tier). Runs no vision models itself.

  (a) recall lookup by model number, UPC, brand or product name: exact match + FTS5 text search (open, rate-limited)
  (b) photo scans: uploads go into a small in-memory queue; a self-hosted GPU worker running open models polls for
      jobs over HTTPS, runs the full pipeline and posts back a result, which this server validates and renders
  (c) a gallery of example scans

Data: a text-only SQLite built nightly by GitHub Actions and published as a release asset, downloaded on boot.
Configuration is environment-only (see .env.example).

    uvicorn demo.server:app --port 8001 --no-access-log
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path as FsPath
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Path, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartParser

from demo import analytics, cards, security
from demo import store as signup_store
from demo.jobs import MAX_BATCH, JobQueue, QueueFull, TooManyPending

# Keep uploaded files in memory while the form is parsed (the default spills anything over 1 MB to a temp file);
# the size guard below caps how much a request can send in the first place.
MultiPartParser.spool_max_size = security.MAX_UPLOAD_BYTES + 1024 * 1024
UPLOAD_LIMITS = {"/api/jobs": 2 * security.MAX_UPLOAD_BYTES + 1024 * 1024, "/api/samples/recalled": 4096,
                 "/api/samples/check": 4096, "/api/samples/nomatch": 4096,
                 "/api/batches": MAX_BATCH * security.MAX_UPLOAD_BYTES + 1024 * 1024}

log = logging.getLogger("uvicorn.error")
ROOT = FsPath(__file__).parent
DB = FsPath(os.environ.get("CTL_DEMO_DB", "data/demo.db"))
DB_URL = os.environ.get(
    "CTL_DEMO_DB_URL", "https://github.com/vinmay/cleartolist/releases/download/data-latest/demo.db"
)
GALLERY = ROOT / "gallery"
WORKER_FRESH_S = 45  # scanning is offered only while the worker has polled this recently
UNAVAILABLE = "Scanning is temporarily unavailable. Model number lookup still works."
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
CHILD = ("crib_bassinet_sleeper", "stroller", "high_chair", "play_yard", "toy", "other_child")


def ensure_db() -> None:
    if DB.exists():
        return
    DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".part")
    try:
        urllib.request.urlretrieve(DB_URL, tmp)  # noqa: S310 — fixed https URL from config
    except urllib.error.HTTPError as e:
        hint = (" Release assets of a private GitHub repo return 404 to anonymous downloads: make the repo public, "
                "or point CTL_DEMO_DB_URL at a public copy of demo.db.") if e.code == 404 else ""
        raise RuntimeError(f"Could not download the demo database from {DB_URL}: HTTP {e.code}.{hint}") from e
    tmp.replace(DB)


async def _flush_counts_every_minute() -> None:
    import asyncio

    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(COUNTS.flush)


@asynccontextmanager
async def lifespan(_: FastAPI):
    import asyncio

    ensure_db()
    task = asyncio.create_task(_flush_counts_every_minute())
    yield
    task.cancel()
    COUNTS.flush()


app = FastAPI(title="ClearToList", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
if GALLERY.exists():
    app.mount("/gallery", StaticFiles(directory=GALLERY), name="gallery")

SAMPLES_DIR = ROOT / "static" / "samples"
SAMPLES = {  # real scans of fixed photos (see static/samples/ATTRIBUTION.md), one per verdict
    "recalled": {"item": "recalled-item.jpg", "label": "recalled-label.jpg"},
    "check": {"item": "check-item.jpg"},
    "nomatch": {"item": "nomatch-item.jpg"},
}
QUOTA_USED_UP = ("Today's free checks are used up. Model number lookup still works, and you can request an invite code "
                 "below.")


class DailyQuota:
    """Photo scans anyone can run without an invite code, per UTC day, across all visitors.

    Kept in memory: a restart resets the day's count, so the cap can only be exceeded, never undercounted, by
    restarts (the per-client rate limits still apply)."""

    def __init__(self) -> None:
        self.day, self.used = "", 0

    def limit(self) -> int:
        v = os.environ.get("CTL_PUBLIC_DAILY_SCANS", "50").strip()
        return int(v) if v.isdigit() else 50

    def _roll(self) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self.day:
            self.day, self.used = today, 0

    def left(self) -> int:
        self._roll()
        return max(0, self.limit() - self.used)

    def take(self, n: int) -> bool:
        self._roll()
        if self.used + n > self.limit():
            return False
        self.used += n
        return True

    def refund(self, n: int) -> None:
        self.used = max(0, self.used - n)


QUEUE = JobQueue()
QUOTA = DailyQuota()
STORE = signup_store.from_env()
COUNTS = analytics.Counts(STORE)
EVENT_LIMIT = security.RateLimiter(60, 60)  # usage events per client per minute
STATS_LIMIT = security.RateLimiter(10, 600)  # stats page attempts per client per 10 min
SIGNUP_LIMIT = security.RateLimiter(5, 3600)  # invite requests per client per hour
NONCES = security.NonceStore()
STATE: dict[str, Any] = {"worker_seen": 0.0, "uploads_override": None}
SCAN_LIMIT = security.RateLimiter(20, 600)  # photos per client per 10 min (a batch of 10 costs 10)
LOOKUP_LIMIT = security.RateLimiter(30, 60)
POLL_LIMIT = security.RateLimiter(120, 60)  # result polling


# --- configuration (read per request, so the kill switch needs no redeploy of code) -------------------------

def env_secret(name: str) -> str | None:
    """A secret from the environment, ignoring whitespace or quotes picked up when pasting it into a dashboard."""
    v = os.environ.get(name)
    return v.strip().strip("'\"").strip() if v else v


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def uploads_enabled() -> bool:
    """Kill switch. Off unless CTL_UPLOADS_ENABLED is set; the admin endpoint can override it at runtime."""
    if STATE["uploads_override"] is not None:
        return bool(STATE["uploads_override"])
    return env_flag("CTL_UPLOADS_ENABLED")


def invite_codes() -> list[str]:
    return [c.strip().strip("'\"") for c in (env_secret("CTL_INVITE_CODES") or "").split(",")
            if len(c.strip().strip("'\"")) >= 6]


def worker_alive(now: float | None = None) -> bool:
    return (time.time() if now is None else now) - STATE["worker_seen"] < WORKER_FRESH_S


# --- middleware: HTTPS, security headers, no stack traces ---------------------------------------------------

CSP = ("default-src 'self'; img-src 'self' blob: https://www.cpsc.gov https://cpsc.gov; style-src 'self'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
       "object-src 'none'")


@app.middleware("http")
async def harden(request: Request, call_next):
    if request.headers.get("x-forwarded-proto") == "http":  # behind Render's TLS proxy: force HTTPS
        return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)
    cap = UPLOAD_LIMITS.get(request.url.path) if request.method == "POST" else None
    if cap is not None:  # refuse oversized or unsized upload bodies before parsing anything
        length = request.headers.get("content-length", "")
        if not length.isdigit():
            return JSONResponse({"error": "Upload size missing. Please try again."}, status_code=411)
        if int(length) > cap:
            return JSONResponse({"error": "Too much data in one upload. Each photo can be up to 8 MB."},
                                status_code=413)
    try:
        resp = await call_next(request)
    except Exception:  # never leak a stack trace
        resp = JSONResponse({"error": "Something went wrong."}, status_code=500)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.headers.get("x-forwarded-proto") == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith(("/api/", "/worker/", "/admin/")):
        resp.headers["Cache-Control"] = "no-store"
    elif request.url.path.endswith((".js", ".css", ".html")) or request.url.path in ("/", "/privacy", "/terms"):
        resp.headers["Cache-Control"] = "no-cache"  # reuse only after checking with the server, so deploys show up
    return resp


@app.exception_handler(RequestValidationError)
async def bad_request(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse({"error": "Invalid request."}, status_code=400)  # never echo the input back


_JSON_PREFIXES = ("/api/", "/worker/", "/admin/")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, e: StarletteHTTPException) -> Response:
    """JSON errors for the API; a plain HTML page for anything else people open in a browser that isn't there."""
    if e.status_code == 404 and not request.url.path.startswith(_JSON_PREFIXES):
        return HTMLResponse((ROOT / "static" / "404.html").read_text(), status_code=404)
    return JSONResponse({"error": e.detail if isinstance(e.detail, str) else "Request failed."},
                        status_code=e.status_code, headers=getattr(e, "headers", None))


def client_of(request: Request) -> str:
    return security.client_key(request.headers.get("x-forwarded-for"),
                               request.client.host if request.client else None)


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True  # non-browser clients; still need an invite code
    host = request.headers.get("host", "")
    return origin in (f"https://{host}", f"http://{host}")


# --- data ----------------------------------------------------------------------------------------------------

def conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def norm_model(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def norm_upc(s: str) -> str:
    d = re.sub(r"\D", "", s)
    return d[1:] if len(d) == 13 and d.startswith("0") else d


def fts_query(q: str) -> str:
    words = re.findall(r"[\w']+", q.lower())
    return " ".join(f'"{w}"' for w in words[:12])


def models_of(c: sqlite3.Connection, recall_id: int) -> list[str]:
    return [m for (m,) in c.execute("SELECT DISTINCT model FROM model_numbers WHERE recall_id=? ORDER BY model",
                                     (recall_id,))]


def row(r: sqlite3.Row, why: str, c: sqlite3.Connection | None = None) -> dict[str, Any]:
    d = {
        "recall_id": r["recall_id"], "recall_number": r["recall_number"], "date": r["recall_date"],
        "title": r["title"], "url": r["url"], "category": r["category"],
        "hazards": json.loads(r["hazards"] or "[]"), "remedies": json.loads(r["remedies"] or "[]"),
        "manufacturers": json.loads(r["manufacturers"] or "[]"), "products": r["products"],
        "image_urls": json.loads(r["image_urls"] or "[]"), "match": why,
    }
    return d | cards.card_fields(d, models_of(c or conn(), r["recall_id"]))


def lookup(q: str, children_only: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    c = conn()
    out: dict[int, dict[str, Any]] = {}
    cat_ok = (lambda cat: cat in CHILD) if children_only else (lambda cat: True)

    m, u = norm_model(q), norm_upc(q)
    if len(u) >= 11:
        for r in c.execute("SELECT r.* FROM upcs x JOIN recalls r USING(recall_id) WHERE x.upc=?", (u,)):
            out.setdefault(r["recall_id"], row(r, f"UPC {u}", c))
    if 4 <= len(m) <= 20 and re.search(r"\d", m):
        for r in c.execute("SELECT r.* FROM model_numbers x JOIN recalls r USING(recall_id) WHERE x.model=?", (m,)):
            out.setdefault(r["recall_id"], row(r, f"model number {m}", c))
    fq = fts_query(q)
    if fq:
        sql = ("SELECT r.* FROM recalls_fts f JOIN recalls r ON r.recall_id=f.rowid WHERE recalls_fts MATCH ? "
               "ORDER BY bm25(recalls_fts, 10.0, 5.0, 5.0, 1.0) LIMIT ?")
        # All words first, then any word, so "graco stroller" still returns Graco and stroller recalls.
        for expr, why in ((fq, "text search"), (fq.replace('" "', '" OR "'), "partial text match")):
            for r in c.execute(sql, (expr, limit * 3)):
                out.setdefault(r["recall_id"], row(r, why, c))
            if len(out) >= limit:
                break
    res = [x for x in out.values() if cat_ok(x["category"])]
    return res[:limit]


# --- public pages and lookup ---------------------------------------------------------------------------------

def count_visit(ref: str | None) -> None:
    COUNTS.add("page_view")
    COUNTS.add(analytics.ref_metric(ref))


@app.get("/", response_class=HTMLResponse)
def index(ref: Annotated[str | None, Query(max_length=40)] = None) -> str:
    count_visit(ref)  # the ?ref= tag only, reduced to an allowlist; no referrer header, no URL
    return (ROOT / "static" / "index.html").read_text()


SITE_TITLE = "ClearToList: Is it recalled? Check before you list or buy."
SITE_DESC = "Check secondhand items against official U.S. recalls before you list or buy. Free, with a photo."
PUBLIC_URL = os.environ.get("CTL_PUBLIC_URL", "https://cleartolist.onrender.com").rstrip("/")


def recall_by_id(recall_id: int) -> dict[str, Any] | None:
    c = conn()
    r = c.execute("SELECT * FROM recalls WHERE recall_id=?", (recall_id,)).fetchone()
    return row(r, "shared link", c) if r is not None else None


@app.get("/api/recall/{recall_id}")
def api_recall(request: Request, recall_id: Annotated[int, Path(ge=1, le=10_000_000)]) -> dict[str, Any]:
    if not LOOKUP_LIMIT.allow(client_of(request)):
        raise HTTPException(429, "Too many requests. Please wait a minute.")
    d = recall_by_id(recall_id)
    if d is None:
        raise HTTPException(404, "No recall with that number.")
    return d


@app.get("/recall/{recall_id}", response_class=HTMLResponse)
def recall_page(recall_id: Annotated[int, Path(ge=1, le=10_000_000)],
                ref: Annotated[str | None, Query(max_length=40)] = None) -> HTMLResponse:
    """A shareable link to one recall (its id only, never a photo). The page is the homepage with this recall shown
    on top; the preview tags carry the recall's short name, escaped."""
    import html

    count_visit(ref)
    d = recall_by_id(recall_id)
    page = (ROOT / "static" / "index.html").read_text()
    if d is None:
        return HTMLResponse(page, status_code=404)
    title = html.escape(f"Recalled: {d['heading']}", quote=True)
    desc = html.escape(f"{d['hazard_tag']} hazard. {d['what_to_do'] or 'See the CPSC notice.'} "
                       "Check your own items with ClearToList.", quote=True)
    url = f"{PUBLIC_URL}/recall/{recall_id}"
    for old, new in [(f"<title>{SITE_TITLE}</title>", f"<title>{title}</title>"),
                     (f'content="{SITE_TITLE}"', f'content="{title}"'),
                     (f'content="{SITE_DESC}"', f'content="{desc}"'),
                     (f'"{PUBLIC_URL}/"', f'"{url}"')]:
        page = page.replace(old, new)
    return HTMLResponse(page)


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> str:
    return (ROOT / "static" / "privacy.html").read_text()


@app.get("/terms", response_class=HTMLResponse)
def terms_page() -> str:
    return (ROOT / "static" / "terms.html").read_text()


@app.get("/api/lookup")
def api_lookup(request: Request, q: str = Query(..., min_length=2, max_length=120),
               children: bool = False) -> dict[str, Any]:
    if not LOOKUP_LIMIT.allow(client_of(request)):
        raise HTTPException(429, "Too many searches. Please wait a minute.")
    COUNTS.add("lookup")
    return {"query": q, "results": lookup(q, children)}


@app.get("/api/gallery")
def api_gallery(set: Literal["curated", "all"] = "all") -> list[dict[str, Any]]:  # noqa: A002
    """The homepage examples (one per verdict)."""
    p = GALLERY / "gallery.json"
    items = json.loads(p.read_text()) if p.exists() else []
    items = items if set == "all" else [i for i in items if i.get("set") == set]
    c = conn()
    for it in items:  # add the short card fields from this server's recall data
        for step in (it.get("item"), it.get("final"), it.get("truth")):
            for rec in (step or {}).get("recalls", []) + ([step] if step and "outcome" in step else []):
                r = c.execute("SELECT * FROM recalls WHERE url=?", (rec.get("url"),)).fetchone()
                if r is not None:
                    full = row(r, "", c)
                    rec.update({k: full[k] for k in ("recall_id", "heading", "hazard_tag", "models", "models_more",
                                                     "what_to_do", "date")})
    return items



@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    try:
        meta: dict[str, Any] = dict(conn().execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as e:
        raise HTTPException(503, "Recall data is loading.") from e
    # No scanning or worker-availability flag here or on any other read-only endpoint: availability is only learned
    # by submitting a scan (rate-limited), and the answer is the same generic message as the kill switch.
    meta["free_scans_today"] = QUOTA.left() > 0
    meta["invite_requests"] = STORE.available
    return meta


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": DB.exists()}


# --- scans: public side --------------------------------------------------------------------------------------

async def read_capped(f: UploadFile) -> bytes:
    data = await f.read(security.MAX_UPLOAD_BYTES + 1)
    await f.close()
    return data


def admit(request: Request, invite: str, cost: int, sample: bool = False) -> tuple[str, bool]:
    """Checks every photo job goes through, before any photo is read. Returns (client key, uses the free quota).

    Order: cross-site guard, kill switch, invite code (if one was given), per-client rate limit, worker available.
    Without a code, the job draws on the public daily quota, which is taken last (see take_quota)."""
    if not same_origin(request):
        raise HTTPException(403, "Cross-site requests are not allowed.")
    if not uploads_enabled():
        QUEUE.count("rejected_disabled")
        raise HTTPException(503, UNAVAILABLE)
    public = False
    if not sample:
        invite = (invite or "").strip()
        if invite:
            codes = invite_codes()
            if not codes or not security.code_matches(invite, codes):
                QUEUE.count("rejected_invite")
                raise HTTPException(403, "That invite code isn't valid.")
        else:
            public = True
            if QUOTA.left() < cost:
                QUEUE.count("rejected_quota")
                raise HTTPException(429, QUOTA_USED_UP)
    client = client_of(request)
    if not SCAN_LIMIT.allow(client, cost=cost):
        QUEUE.count("rejected_rate")
        raise HTTPException(429, "Too many photos from this connection. Please wait a few minutes.")
    if not worker_alive():
        QUEUE.count("rejected_unavailable")
        raise HTTPException(503, UNAVAILABLE)
    return client, public


def enqueue(client: str, photos: dict[str, bytes], public: bool, kind: str = "single") -> Any:
    """Take the free quota (if used) only now that the photos are valid; give it back if the queue says no."""
    if public and not QUOTA.take(len(photos)):
        QUEUE.count("rejected_quota")
        raise HTTPException(429, QUOTA_USED_UP)
    try:
        return QUEUE.submit(client, photos, kind=kind)
    except (QueueFull, TooManyPending) as e:
        if public:
            QUOTA.refund(len(photos))
        if isinstance(e, QueueFull):
            raise HTTPException(503, "The scanner is busy right now. Please try again in a few minutes.") from e
        raise HTTPException(429, "You already have scans in progress. Wait for them to finish.") from e


@app.post("/api/jobs", status_code=202)
async def create_job(request: Request, photo: Annotated[UploadFile, File()],
                     label: Annotated[UploadFile | None, File()] = None,
                     invite: Annotated[str, Form(max_length=64)] = "") -> dict[str, Any]:
    has_label = label is not None and bool(label.filename)
    client, public = admit(request, invite, 1 + has_label)
    photos = {}
    try:
        photos["item"] = security.sanitize_image(await read_capped(photo))
        if has_label:
            photos["label"] = security.sanitize_image(await read_capped(label))
    except security.UploadError as e:
        QUEUE.count("rejected_upload")
        raise HTTPException(400, str(e)) from e
    job = enqueue(client, photos, public)
    COUNTS.add("real_scan")
    return {"job": job.id, "state": job.state}


@app.post("/api/samples/{name}", status_code=202)
async def create_sample(request: Request, name: str) -> dict[str, Any]:
    """'Try it with a sample photo': a real scan of a fixed photo through the normal queue. No code or quota
    needed, but the same kill switch, rate limit and queue caps apply."""
    if name not in SAMPLES:
        raise HTTPException(404, "No such sample.")
    files = SAMPLES[name]
    client, _ = admit(request, "", len(files), sample=True)
    photos = {k: security.sanitize_image((SAMPLES_DIR / f).read_bytes()) for k, f in files.items()}
    job = enqueue(client, photos, public=False)
    QUEUE.count("sample")
    COUNTS.add("sample_scan")
    return {"job": job.id, "state": job.state}


@app.post("/api/batches", status_code=202)
async def create_batch(request: Request, photos: Annotated[list[UploadFile], File()],
                       invite: Annotated[str, Form(max_length=64)] = "") -> dict[str, Any]:
    """Up to MAX_BATCH item photos in one job and one combined report. Same checks as a single scan; the batch
    counts once per photo against the per-client and global queue limits, the rate limit and the free quota."""
    files = [f for f in photos if f.filename]
    if not files:
        raise HTTPException(400, "Choose at least one photo.")
    if len(files) > MAX_BATCH:
        raise HTTPException(400, f"A batch can have up to {MAX_BATCH} photos. Remove {len(files) - MAX_BATCH}.")
    client, public = admit(request, invite, len(files))
    clean = {}
    for i, f in enumerate(files):
        try:
            clean[f"b{i}"] = security.sanitize_image(await read_capped(f))
        except security.UploadError as e:
            QUEUE.count("rejected_upload")
            raise HTTPException(400, f"Photo {i + 1}: {e}") from e
    job = enqueue(client, clean, public, kind="batch")
    COUNTS.add("batch_scan")
    return {"job": job.id, "state": job.state, "photos": len(clean)}


@app.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str) -> dict[str, Any]:
    if not POLL_LIMIT.allow(client_of(request)):
        raise HTTPException(429, "Too many requests. Please slow down.")
    job = QUEUE.get(job_id) if JOB_ID.match(job_id) else None
    if job is None:
        raise HTTPException(404, "This scan has expired or doesn't exist.")
    if job.state in ("queued", "running"):
        return {"state": job.state}
    if job.state == "done" and job.result is not None:
        return {"state": "done", "kind": job.kind, "result": job.result}
    return {"state": "unavailable", "message": UNAVAILABLE}  # failed or expired: same generic message


# --- scans: worker side (HMAC-signed requests only) ----------------------------------------------------------

async def require_worker(request: Request) -> bytes:
    body = await request.body()
    secret = env_secret("CTL_WORKER_SECRET")
    why = "body too large" if len(body) > 64_000 else security.check(
        secret, request.method, request.url.path, body, {k.lower(): v for k, v in request.headers.items()}, NONCES)
    if why != "ok":
        # Private server log only (never sent back): the reason plus a fingerprint of the configured secret, at most
        # once a minute, so the owner can tell a missing, short or mismatched secret from clock skew.
        if time.time() - STATE.get("auth_logged", 0.0) > 60:
            STATE["auth_logged"] = time.time()
            log.warning("worker auth failed: %s (configured secret fingerprint %s, length %d)",
                        why, security.fingerprint(secret), len(secret or ""))
        raise HTTPException(401, "Unauthorized.")
    return body


@app.post("/worker/claim")
async def worker_claim(request: Request) -> Response:
    await require_worker(request)
    STATE["worker_seen"] = time.time()
    job = QUEUE.claim()
    if job is None:
        return Response(status_code=204)
    return JSONResponse({"job": job.id, "kind": job.kind, "photos": sorted(job.photos)})


@app.get("/worker/jobs/{job_id}/photo/{which}")
async def worker_photo(request: Request, job_id: str,
                       which: Annotated[str, Path(pattern=r"^(item|label|b[0-9])$")]) -> Response:
    await require_worker(request)
    job = QUEUE.get(job_id) if JOB_ID.match(job_id) else None
    if job is None or job.state != "running" or which not in job.photos:
        raise HTTPException(404, "Not found.")
    return Response(job.photos[which], media_type="image/jpeg")


NOTE = Literal["label_ask", "label_no_code", "label_code_unlisted", "label_other_recall", "car_seat"]
CODE = re.compile(r"^(model|upc):[A-Z0-9]{4,20}$")


class Read(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field("", max_length=80)
    brand: str = Field("", max_length=80)
    model_number: str = Field("", max_length=40)
    upc: str = Field("", max_length=20)


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recall_id: int = Field(ge=1, le=10_000_000)
    codes: list[str] = Field(default_factory=list, max_length=6)


class WorkerResult(BaseModel):
    """Everything the worker may send. Recall titles, links and photos come from this server's own database."""
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["CONFIRMED", "POSSIBLE MATCH", "NO MATCH FOUND"]
    shortlist: list[Pick] = Field(default_factory=list, max_length=3)
    read: Read = Field(default_factory=Read)
    label_read: Read | None = None
    notes: list[NOTE] = Field(default_factory=list, max_length=4)
    closest: bool = False  # NO MATCH FOUND with the three closest recalls shown as suggestions, not matches
    seconds: float = Field(0, ge=0, le=600)


class BatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[WorkerResult] = Field(min_length=1, max_length=MAX_BATCH)


def code_in_db(c: sqlite3.Connection, recall_id: int, code: str) -> bool:
    kind, value = code.split(":", 1)
    table, col = ("model_numbers", "model") if kind == "model" else ("upcs", "upc")
    return c.execute(f"SELECT 1 FROM {table} WHERE recall_id=? AND {col}=?", (recall_id, value)).fetchone() is not None


def render_result(w: WorkerResult) -> dict[str, Any]:
    """Re-check the worker's result against this server's data and build what the page shows."""
    c = conn()
    cards = []
    for p in w.shortlist:
        r = c.execute("SELECT * FROM recalls WHERE recall_id=?", (p.recall_id,)).fetchone()
        if r is None:
            continue
        codes = [x for x in p.codes if CODE.match(x) and code_in_db(c, p.recall_id, x)]
        card = row(r, "", c)
        card["codes"] = codes
        cards.append(card)
    verdict = w.verdict
    if verdict == "CONFIRMED" and not (cards and cards[0]["codes"]):
        verdict = "POSSIBLE MATCH" if cards else "NO MATCH FOUND"  # an unverifiable code claim is not an alarm
        QUEUE.count("downgraded_confirmed")
    if verdict == "CONFIRMED":
        cards = cards[:1]
    if verdict == "POSSIBLE MATCH" and not cards:
        verdict = "NO MATCH FOUND"
    closest = verdict == "NO MATCH FOUND" and w.closest and bool(cards)
    if verdict == "NO MATCH FOUND":
        cards = [{**c, "codes": []} for c in cards] if closest else []
    return {"verdict": verdict, "closest": closest, "recalls": cards, "read": w.read.model_dump(),
            "label_read": w.label_read.model_dump() if w.label_read else None,
            "notes": list(dict.fromkeys(w.notes)), "seconds": round(w.seconds, 1)}


@app.post("/worker/jobs/{job_id}/result")
async def worker_result(request: Request, job_id: str) -> dict[str, bool]:
    body = await require_worker(request)
    if not JOB_ID.match(job_id):
        raise HTTPException(404, "Not found.")
    job = QUEUE.get(job_id)
    try:
        if job is not None and job.kind == "batch":
            b = BatchResult.model_validate_json(body)
            if len(b.items) != job.weight:
                raise ValueError("one result per photo")
            rendered: dict[str, Any] = {"items": [render_result(w) for w in b.items]}
        else:
            rendered = render_result(WorkerResult.model_validate_json(body))
    except (ValidationError, ValueError) as e:
        QUEUE.finish(job_id, None)
        raise HTTPException(400, "Invalid result.") from e
    for r in rendered.get("items", [rendered]):
        COUNTS.add(f"verdict_{analytics.TIERS[r['verdict']]}")
    if not QUEUE.finish(job_id, rendered):
        raise HTTPException(409, "Job is not running.")
    return {"ok": True}


@app.post("/worker/jobs/{job_id}/fail")
async def worker_fail(request: Request, job_id: str) -> dict[str, bool]:
    await require_worker(request)
    return {"ok": JOB_ID.match(job_id) is not None and QUEUE.finish(job_id, None)}


# --- invite requests ------------------------------------------------------------------------------------------

class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    consent: bool = False
    alerts: bool = False
    categories: list[str] = Field(default_factory=list, max_length=len(signup_store.ALERT_CATEGORIES))
    website: str = Field("", max_length=200)  # honeypot: hidden from people, filled in by bots


@app.post("/api/invite-requests", status_code=202)
async def invite_request(request: Request, body: InviteRequest) -> dict[str, Any]:
    """Store an email address (and optional alert categories) to send an invite code to. No address is logged."""
    if not same_origin(request):
        raise HTTPException(403, "Cross-site requests are not allowed.")
    if not SIGNUP_LIMIT.allow(client_of(request)):
        raise HTTPException(429, "Too many requests from this connection. Please try again in an hour.")
    thanks = {"ok": True, "message": "Thanks. We'll email you an invite code."}
    if body.website:
        QUEUE.count("signup_honeypot")
        return thanks  # look normal to the bot, store nothing
    if not body.consent:
        raise HTTPException(400, "Tick the box to agree that we can store your email address for this.")
    email = body.email.strip()
    if not signup_store.EMAIL.match(email):
        raise HTTPException(400, "That doesn't look like an email address. Check it for typos.")
    cats = [c for c in dict.fromkeys(body.categories) if c in signup_store.ALERT_CATEGORIES]
    if len(cats) != len(set(body.categories)):
        raise HTTPException(400, "Choose categories from the list.")
    if body.alerts and not cats:
        raise HTTPException(400, "Choose at least one category for recall alerts, or untick the alerts box.")
    if not STORE.available:
        raise HTTPException(503, "Invite requests are closed right now. Please try again later.")
    try:
        STORE.add(email, body.alerts, cats if body.alerts else [])
    except Exception as e:  # never surface database details
        log.warning("invite request not stored: %s", type(e).__name__)
        raise HTTPException(503, "We couldn't save that right now. Please try again in a few minutes.") from e
    QUEUE.count("signup")
    COUNTS.add("invite_request")
    if body.alerts:
        COUNTS.add("alert_signup")
    return thanks


@app.get("/admin/invite-requests")
async def admin_invite_requests(request: Request) -> Response:
    """CSV of invite requests, for the owner only (signed with the admin secret; scripts/export_signups.py)."""
    if not security.verify(env_secret("CTL_ADMIN_SECRET"), request.method, request.url.path, b"",
                           {k.lower(): v for k, v in request.headers.items()}, NONCES):
        raise HTTPException(401, "Unauthorized.")
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "created_at", "alerts", "categories"])
    for row in STORE.all():
        w.writerow(row)
    return Response(buf.getvalue(), media_type="text/csv")


# --- anonymous usage events and the private stats page -------------------------------------------------------------

class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["copy_link", "ask_seller", "helpful"]
    answer: Literal["yes", "no"] | None = None
    tier: Literal["CONFIRMED", "POSSIBLE MATCH", "NO MATCH FOUND"] | None = None


@app.post("/api/events", status_code=204)
def usage_event(request: Request, e: Event) -> Response:
    """Counts a click as a daily total. Nothing identifies who clicked."""
    if not same_origin(request):
        raise HTTPException(403, "Cross-site requests are not allowed.")
    if not EVENT_LIMIT.allow(client_of(request)):
        raise HTTPException(429, "Too many requests.")
    if e.event == "helpful":
        if e.answer is None or e.tier is None:
            raise HTTPException(400, "Invalid request.")
        COUNTS.add(f"helpful_{e.answer}_{analytics.TIERS[e.tier]}")
    else:
        COUNTS.add(e.event)
    return Response(status_code=204)


def admin_password_ok(request: Request) -> bool:
    import base64
    import hmac

    secret = env_secret("CTL_ADMIN_SECRET")
    auth = request.headers.get("authorization", "")
    if not secret or len(secret) < 32 or not auth.lower().startswith("basic "):
        return False
    try:
        _, _, password = base64.b64decode(auth[6:].strip(), validate=True).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(password.encode(), secret.encode())


@app.get("/admin/stats", response_class=HTMLResponse)
def admin_stats(request: Request) -> HTMLResponse:
    """Private daily totals and a simple funnel. HTTP Basic auth, password = the admin secret (any user name)."""
    import html

    if not STATS_LIMIT.allow(client_of(request)):
        raise HTTPException(429, "Too many attempts. Please wait a few minutes.")
    if not admin_password_ok(request):
        raise HTTPException(401, "Unauthorized.", headers={"WWW-Authenticate": 'Basic realm="ClearToList stats"'})
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 29 * 86400))
    days = COUNTS.totals(since)
    total: dict[str, int] = {}
    for d in days.values():
        for k, v in d.items():
            total[k] = total.get(k, 0) + v
    scans = total.get("sample_scan", 0) + total.get("real_scan", 0) + total.get("batch_scan", 0)
    helpful_yes = sum(v for k, v in total.items() if k.startswith("helpful_yes_"))
    funnel = [("Visits", total.get("page_view", 0)), ("Scans", scans), ("Helpful: yes", helpful_yes),
              ("Invite requests", total.get("invite_request", 0))]
    cols = sorted(analytics.METRICS)
    head = "".join(f"<th scope=\"col\">{html.escape(c)}</th>" for c in cols)
    body = "".join(f"<tr><th scope=\"row\">{html.escape(d)}</th>" + "".join(
        f"<td>{days[d].get(c, 0)}</td>" for c in cols) + "</tr>" for d in sorted(days, reverse=True))
    fun = "".join(f"<li><strong>{html.escape(k)}</strong>: {v}</li>" for k, v in funnel)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width, initial-scale=1"><meta name="robots" content="noindex"><title>ClearToList stats</title>
<link rel="stylesheet" href="/static/app.css"></head><body><main class="stats"><h1>Usage, last 30 days</h1>
<p class="muted">Daily totals only. No cookies, IP addresses, user agents or per-person data are stored.</p>
<h2>Funnel</h2><ol class="funnel">{fun}</ol><h2>Daily totals</h2><div class="table-wrap"><table class="results">
<thead><tr><th scope="col">Day</th>{head}</tr></thead><tbody>{body}</tbody></table></div></main></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


# --- admin: instant kill switch and anonymous counts (signed with a separate secret) -------------------------

@app.post("/admin/uploads")
async def admin_uploads(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not security.verify(env_secret("CTL_ADMIN_SECRET"), request.method, request.url.path, body,
                           {k.lower(): v for k, v in request.headers.items()}, NONCES):
        raise HTTPException(401, "Unauthorized.")
    try:
        want = json.loads(body or b"{}").get("enabled")
    except (ValueError, AttributeError) as e:
        raise HTTPException(400, "Invalid request.") from e
    STATE["uploads_override"] = None if want is None else bool(want)
    return {"uploads_enabled": uploads_enabled(), "counts": dict(QUEUE.counts)}


# Registered last so it never shadows a named route.
_ROOT_FILES = {"favicon.ico": "image/x-icon", "favicon.svg": "image/svg+xml", "apple-touch-icon.png": "image/png",
               "robots.txt": "text/plain; charset=utf-8", "sitemap.xml": "application/xml"}


@app.get("/{name}", include_in_schema=False)
def root_file(name: str) -> Response:
    """Files browsers and crawlers ask for at the site root."""
    if name not in _ROOT_FILES:
        raise HTTPException(404, "Not found.")
    return Response((ROOT / "static" / name).read_bytes(), media_type=_ROOT_FILES[name],
                    headers={"Cache-Control": "public, max-age=86400"})
