"""Local web app: single scan and batch check.

    uv run uvicorn app.main:app --port 8000
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from PIL import Image

from ingest.cpsc import DB_PATH, connect
from read.gemma import OllamaVision
from verify.pipeline import DISCLAIMER, scan

ROOT = Path(__file__).parent
DATA = Path("data").resolve()
CACHE = Path("data/scan_cache")
app = FastAPI(title="ClearToList")
_vision: OllamaVision | None = None


def vision() -> OllamaVision:
    global _vision
    if _vision is None:
        _vision = OllamaVision()
    return _vision


def _open(upload: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(upload))
        img.load()
        return img
    except Exception as e:
        raise HTTPException(400, f"not an image: {e}") from e


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "static" / "index.html").read_text()


@app.get("/recall-image/{path:path}")
def recall_image(path: str) -> FileResponse:
    p = (Path("data") / path).resolve()
    if not p.is_relative_to(DATA) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


def _public(result: dict[str, Any]) -> dict[str, Any]:
    for m in result["matches"]:
        if m.get("recall_image"):
            m["recall_image_url"] = "/recall-image/" + str(Path(m["recall_image"]).relative_to("data"))
    return result


def cached_scan(raw: bytes) -> dict[str, Any]:
    """Scan, reusing a saved result for byte-identical photos (re-uploads, batch re-runs, recorded demos)."""
    # Keyed on the recall data version too, so a refresh with new recalls invalidates old results.
    version = connect(DB_PATH).execute("SELECT MAX(last_publish_date), COUNT(*) FROM recalls").fetchone()
    key = hashlib.sha256(raw + repr(version).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        r = json.loads(path.read_text())
        r["cached"] = True
        return r
    r = scan(_open(raw), vision=vision()).to_dict()
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(r))
    r["cached"] = False
    return r


@app.post("/api/scan")
async def api_scan(photo: UploadFile = File(...)) -> dict[str, Any]:
    return _public(cached_scan(await photo.read()))


@app.post("/api/batch")
async def api_batch(photos: list[UploadFile] = File(...)) -> dict[str, Any]:
    rows = []
    for f in photos:
        try:
            r = cached_scan(await f.read())
        except HTTPException as e:
            rows.append({"file": f.filename, "verdict": "ERROR", "error": e.detail})
            continue
        top = r["matches"][0] if r["matches"] else None
        rows.append({
            "file": f.filename, "verdict": r["verdict"],
            "item": (r["read"] or {}).get("category", ""),
            "recall_title": top["title"] if top and r["verdict"] != "NO MATCH FOUND" else "",
            "recall_url": top["url"] if top and r["verdict"] != "NO MATCH FOUND" else "",
            "seconds": r["timings"]["total_s"],
        })
    order = {"CONFIRMED": 0, "LIKELY": 1, "UNSURE": 2, "ERROR": 3, "NO MATCH FOUND": 4}
    rows.sort(key=lambda r: order.get(r["verdict"], 9))
    return {"rows": rows, "disclaimer": DISCLAIMER}


@app.post("/api/batch.csv")
async def api_batch_csv(photos: list[UploadFile] = File(...)) -> StreamingResponse:
    data = await api_batch(photos)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["file", "verdict", "item", "recall_title", "recall_url", "seconds", "error"])
    w.writeheader()
    for r in data["rows"]:
        w.writerow(r)
    buf.write(f"\n# {DISCLAIMER}\n")
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=cleartolist-report.csv"})


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "vision_model": vision().model, "demo": os.environ.get("CTL_DEMO", "0")}


if __name__ == "__main__":
    print(json.dumps(health()))
