"""Self-hosted GPU worker: polls the hosted ClearToList server for scan jobs and runs the open-model pipeline.

    CTL_SERVER_URL=https://<your-service>.onrender.com CTL_WORKER_SECRET=... uv run python -m worker.worker

Security model:
  * Outbound HTTPS to CTL_SERVER_URL only. No listening sockets, no tunnels, no redirects followed.
  * Every request is HMAC-signed with CTL_WORKER_SECRET (timestamp + nonce + body hash; see demo/security.py).
  * Photos are fetched only from the server's own job endpoint, by a path built here from a validated job id.
    The worker never fetches a URL that came from job data and never executes anything derived from it.
  * Downloaded photos are re-validated and re-encoded before use, and kept in memory only.
  * Model output is untrusted: Gemma's reads are schema-constrained JSON, the verdict comes from the rule function
    (verify/verdict.py), and only recall ids, matched codes, enum notes and short read fields are sent back.
"""

from __future__ import annotations

import os

# On a 6 GB card, Gemma 4 E4B and EmbeddingGemma only stay resident together if SigLIP runs on the CPU;
# otherwise Ollama swaps a model in and out on every scan (+20-30 s). Must be set before the models load.
os.environ.setdefault("CTL_IMAGE_DEVICE", "cpu")

import io  # noqa: E402
import re
import sys
import time
from typing import Any

import httpx
from PIL import Image

from demo.security import UploadError, sanitize_image, signed_headers
from verify.pipeline import label_followup, scan

JOB_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")
CODE = re.compile(r"^(model|upc):[A-Z0-9]{4,20}$")
IDLE_POLL_S = 3.0
MAX_BACKOFF_S = 60.0
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def config() -> tuple[str, str]:
    url = os.environ.get("CTL_SERVER_URL", "").rstrip("/")
    secret = os.environ.get("CTL_WORKER_SECRET", "")
    local = (os.environ.get("CTL_WORKER_ALLOW_HTTP") == "1"  # local testing only
             and re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", url))
    if not (re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?$", url) or local):
        sys.exit("CTL_SERVER_URL must be an https:// origin with no path")
    if len(secret) < 32:
        sys.exit("CTL_WORKER_SECRET must be set (32+ characters)")
    return url, secret


def clip(s: Any, n: int) -> str:
    return _CTRL.sub(" ", str(s or ""))[:n]


_JUNK = re.compile(r"[<>{}`\[\]|\\]|tool_call|unreadable|not[ _]visible|none[ _]found|^n/?a$|^none$", re.I)
_UNSAFE = re.compile(r"[^A-Za-z0-9 '&.,/()+#:-]")


def display_field(s: Any, n: int) -> str:
    """A read field fit to show a user: model output is untrusted, so markup-like or placeholder text is dropped
    and anything outside a plain character set is removed. Display only; matching uses the raw read."""
    s = clip(s, 200).strip()
    if not s or _JUNK.search(s):
        return ""
    return re.sub(r"\s+", " ", _UNSAFE.sub("", s)).strip()[:n]


def read_fields(read: dict[str, Any] | None) -> dict[str, str]:
    r = read or {}
    return {"category": display_field(r.get("category"), 80), "brand": display_field(r.get("brand"), 80),
            "model_number": display_field(r.get("model_number"), 40), "upc": display_field(r.get("upc"), 20)}


def to_wire(item: dict[str, Any], final: dict[str, Any], label: dict[str, Any] | None, seconds: float) -> dict:
    """The result as the server accepts it: ids, codes, enums and short read fields. No free text."""
    verdict = final["verdict"]
    shortlist = [{"recall_id": int(m["recall_id"]),
                  "codes": [e for e in m.get("exact", []) if CODE.match(e)][:6]} for m in final["matches"][:3]]
    notes = []
    if label is None and verdict == "POSSIBLE MATCH":
        notes.append("label_ask")
    if label is not None and verdict != "CONFIRMED":
        lr = label.get("read") or {}
        notes.append("label_code_unlisted" if (lr.get("model_number") or lr.get("upc")) else "label_no_code")
    if label is not None and verdict == "CONFIRMED" and item["matches"] and shortlist and \
            shortlist[0]["recall_id"] not in {m["recall_id"] for m in item["matches"]}:
        notes.append("label_other_recall")
    if "car seat" in clip((item.get("read") or {}).get("category"), 80).lower():
        notes.append("car_seat")
    closest = verdict == "NO MATCH FOUND" and bool(final.get("closest")) and bool(shortlist)
    return {"verdict": verdict, "shortlist": shortlist if verdict != "NO MATCH FOUND" or closest else [],
            "closest": closest,
            "read": read_fields(item.get("read")), "label_read": read_fields(label.get("read")) if label else None,
            "notes": notes, "seconds": round(seconds, 2)}


def run_job(photos: dict[str, Image.Image]) -> dict[str, Any]:
    t = time.perf_counter()
    item = scan(photos["item"]).to_dict()
    label, final = None, item
    if "label" in photos:
        label = scan(photos["label"]).to_dict()
        final = label_followup(item, label)
    return to_wire(item, final, label, time.perf_counter() - t)


class Server:
    def __init__(self, url: str, secret: str) -> None:
        self.secret = secret
        self.http = httpx.Client(base_url=url, timeout=30, follow_redirects=False)

    def call(self, method: str, path: str, body: bytes = b"") -> httpx.Response:
        h = signed_headers(self.secret, method, path, body)
        if body:
            h["Content-Type"] = "application/json"
        return self.http.request(method, path, content=body or None, headers=h)

    def photo(self, job: str, which: str) -> Image.Image:
        r = self.call("GET", f"/worker/jobs/{job}/photo/{which}")
        r.raise_for_status()
        if r.headers.get("content-type") != "image/jpeg" or len(r.content) > 9 * 1024 * 1024:
            raise UploadError("unexpected photo response")
        return Image.open(io.BytesIO(sanitize_image(r.content))).convert("RGB")


def main() -> None:
    import json

    url, secret = config()
    srv = Server(url, secret)
    backoff = IDLE_POLL_S
    # Load every model once before taking jobs, so the first user doesn't wait for a cold start.
    t = time.perf_counter()
    scan(Image.new("RGB", (256, 256), (128, 128, 128)))
    print(f"worker ready (models loaded in {time.perf_counter() - t:.0f} s)", flush=True)
    while True:
        try:
            r = srv.call("POST", "/worker/claim")
            if r.status_code == 204:
                backoff = IDLE_POLL_S
                time.sleep(IDLE_POLL_S)
                continue
            r.raise_for_status()
            d = r.json()
            job, which = str(d.get("job", "")), d.get("photos", [])
            if not JOB_ID.match(job) or not isinstance(which, list) or not set(which) <= {"item", "label"} \
                    or "item" not in which:
                print("ignored a malformed job", flush=True)
                continue
            try:
                result = run_job({w: srv.photo(job, w) for w in which})
            except Exception as e:  # report a generic failure; details stay local
                print(f"scan failed: {type(e).__name__}", flush=True)
                srv.call("POST", f"/worker/jobs/{job}/fail")
                continue
            body = json.dumps(result).encode()
            rr = srv.call("POST", f"/worker/jobs/{job}/result", body)
            print(f"scan done: {result['verdict']} in {result['seconds']} s (server {rr.status_code})", flush=True)
            backoff = IDLE_POLL_S
        except (httpx.HTTPError, ValueError) as e:
            print(f"server unreachable or error ({type(e).__name__}); retrying in {backoff:.0f} s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_S)


if __name__ == "__main__":
    main()
