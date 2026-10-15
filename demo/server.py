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
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from demo import security
from demo.jobs import JobQueue, QueueFull, TooManyPending

ROOT = Path(__file__).parent
DB = Path(os.environ.get("CTL_DEMO_DB", "data/demo.db"))
DB_URL = os.environ.get(
    "CTL_DEMO_DB_URL", "https://github.com/vinmay/cleartolist/releases/download/data-latest/demo.db"
)
GALLERY = ROOT / "gallery"
WORKER_FRESH_S = 45  # scanning is offered only while the worker has polled this recently
UNAVAILABLE = "Scanning is temporarily unavailable. Recall lookup still works."
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_db()
    yield


app = FastAPI(title="ClearToList", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
if GALLERY.exists():
    app.mount("/gallery", StaticFiles(directory=GALLERY), name="gallery")

QUEUE = JobQueue()
NONCES = security.NonceStore()
STATE: dict[str, Any] = {"worker_seen": 0.0, "uploads_override": None}
SCAN_LIMIT = security.RateLimiter(5, 600)  # photo scans per client per 10 min
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
    return resp


@app.exception_handler(RequestValidationError)
async def bad_request(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse({"error": "Invalid request."}, status_code=400)  # never echo the input back


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, e: StarletteHTTPException) -> JSONResponse:
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


def row(r: sqlite3.Row, why: str) -> dict[str, Any]:
    return {
        "recall_id": r["recall_id"], "recall_number": r["recall_number"], "date": r["recall_date"],
        "title": r["title"], "url": r["url"], "category": r["category"],
        "hazards": json.loads(r["hazards"] or "[]"), "remedies": json.loads(r["remedies"] or "[]"),
        "manufacturers": json.loads(r["manufacturers"] or "[]"), "products": r["products"],
        "image_urls": json.loads(r["image_urls"] or "[]"), "match": why,
    }


def lookup(q: str, children_only: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    c = conn()
    out: dict[int, dict[str, Any]] = {}
    cat_ok = (lambda cat: cat in CHILD) if children_only else (lambda cat: True)

    m, u = norm_model(q), norm_upc(q)
    if len(u) >= 11:
        for r in c.execute("SELECT r.* FROM upcs x JOIN recalls r USING(recall_id) WHERE x.upc=?", (u,)):
            out.setdefault(r["recall_id"], row(r, f"UPC {u}"))
    if 4 <= len(m) <= 20 and re.search(r"\d", m):
        for r in c.execute("SELECT r.* FROM model_numbers x JOIN recalls r USING(recall_id) WHERE x.model=?", (m,)):
            out.setdefault(r["recall_id"], row(r, f"model number {m}"))
    fq = fts_query(q)
    if fq:
        sql = ("SELECT r.* FROM recalls_fts f JOIN recalls r ON r.recall_id=f.rowid WHERE recalls_fts MATCH ? "
               "ORDER BY bm25(recalls_fts, 10.0, 5.0, 5.0, 1.0) LIMIT ?")
        # All words first, then any word, so "graco stroller" still returns Graco and stroller recalls.
        for expr, why in ((fq, "text search"), (fq.replace('" "', '" OR "'), "partial text match")):
            for r in c.execute(sql, (expr, limit * 3)):
                out.setdefault(r["recall_id"], row(r, why))
            if len(out) >= limit:
                break
    res = [x for x in out.values() if cat_ok(x["category"])]
    return res[:limit]


# --- public pages and lookup ---------------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "static" / "index.html").read_text()


@app.get("/api/lookup")
def api_lookup(request: Request, q: str = Query(..., min_length=2, max_length=120),
               children: bool = False) -> dict[str, Any]:
    if not LOOKUP_LIMIT.allow(client_of(request)):
        raise HTTPException(429, "Too many searches. Please wait a minute.")
    return {"query": q, "results": lookup(q, children)}


@app.get("/api/gallery")
def api_gallery() -> list[dict[str, Any]]:
    p = GALLERY / "gallery.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.get("/api/meta")
def api_meta() -> dict[str, Any]:
    try:
        meta: dict[str, Any] = dict(conn().execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as e:
        raise HTTPException(503, "Recall data is loading.") from e
    meta["scanning"] = uploads_enabled() and bool(invite_codes())  # configured, not whether a worker is up
    return meta


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": DB.exists()}


# --- scans: public side --------------------------------------------------------------------------------------

async def read_capped(f: UploadFile) -> bytes:
    data = await f.read(security.MAX_UPLOAD_BYTES + 1)
    await f.close()
    return data


@app.post("/api/jobs", status_code=202)
async def create_job(request: Request, invite: Annotated[str, Form(max_length=64)],
                     photo: Annotated[UploadFile, File()],
                     label: Annotated[UploadFile | None, File()] = None) -> dict[str, Any]:
    if not same_origin(request):
        raise HTTPException(403, "Cross-site requests are not allowed.")
    if not uploads_enabled():
        QUEUE.count("rejected_disabled")
        raise HTTPException(503, UNAVAILABLE)
    codes = invite_codes()
    if not codes or not security.code_matches(invite, codes):
        QUEUE.count("rejected_invite")
        raise HTTPException(403, "That invite code isn't valid.")
    client = client_of(request)
    if not SCAN_LIMIT.allow(client):
        QUEUE.count("rejected_rate")
        raise HTTPException(429, "Too many scans from this connection. Please wait a few minutes.")
    if not worker_alive():
        QUEUE.count("rejected_unavailable")
        raise HTTPException(503, UNAVAILABLE)
    photos = {}
    try:
        photos["item"] = security.sanitize_image(await read_capped(photo))
        if label is not None and label.filename:
            photos["label"] = security.sanitize_image(await read_capped(label))
    except security.UploadError as e:
        QUEUE.count("rejected_upload")
        raise HTTPException(400, str(e)) from e
    try:
        job = QUEUE.submit(client, photos)
    except QueueFull as e:
        raise HTTPException(503, "The scanner is busy right now. Please try again in a few minutes.") from e
    except TooManyPending as e:
        raise HTTPException(429, "You already have scans in progress. Wait for them to finish.") from e
    return {"job": job.id, "state": job.state}


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
        return {"state": "done", "result": job.result}
    return {"state": "unavailable", "message": UNAVAILABLE}  # failed or expired: same generic message


# --- scans: worker side (HMAC-signed requests only) ----------------------------------------------------------

async def require_worker(request: Request) -> bytes:
    body = await request.body()
    if len(body) > 64_000 or not security.verify(
            env_secret("CTL_WORKER_SECRET"), request.method, request.url.path, body,
            {k.lower(): v for k, v in request.headers.items()}, NONCES):
        raise HTTPException(401, "Unauthorized.")
    return body


@app.post("/worker/claim")
async def worker_claim(request: Request) -> Response:
    await require_worker(request)
    STATE["worker_seen"] = time.time()
    job = QUEUE.claim()
    if job is None:
        return Response(status_code=204)
    return JSONResponse({"job": job.id, "photos": sorted(job.photos)})


@app.get("/worker/jobs/{job_id}/photo/{which}")
async def worker_photo(request: Request, job_id: str, which: Literal["item", "label"]) -> Response:
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
        card = row(r, "")
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
    try:
        w = WorkerResult.model_validate_json(body)
    except ValidationError as e:
        QUEUE.finish(job_id, None)
        raise HTTPException(400, "Invalid result.") from e
    if not QUEUE.finish(job_id, render_result(w)):
        raise HTTPException(409, "Job is not running.")
    return {"ok": True}


@app.post("/worker/jobs/{job_id}/fail")
async def worker_fail(request: Request, job_id: str) -> dict[str, bool]:
    await require_worker(request)
    return {"ok": JOB_ID.match(job_id) is not None and QUEUE.finish(job_id, None)}


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
