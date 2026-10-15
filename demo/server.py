"""Public lightweight demo (Render free tier). Runs no vision models.

  (a) recall lookup by model number, UPC, brand or product name: exact match + FTS5 text search
  (b) a gallery of photo scans that were run locally with the full Gemma pipeline

Data: a text-only SQLite built nightly by GitHub Actions and published as a release asset.
On boot the server downloads it to $CTL_DEMO_DB (default data/demo.db) if it is missing.

    uvicorn demo.server:app --port 8001
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
DB = Path(os.environ.get("CTL_DEMO_DB", "data/demo.db"))
DB_URL = os.environ.get(
    "CTL_DEMO_DB_URL", "https://github.com/vinmay/cleartolist/releases/download/data-latest/demo.db"
)
GALLERY = ROOT / "gallery"


def ensure_db() -> None:
    if DB.exists():
        return
    DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".part")
    urllib.request.urlretrieve(DB_URL, tmp)  # noqa: S310 — fixed https URL from config
    tmp.replace(DB)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_db()
    yield


app = FastAPI(title="ClearToList demo", lifespan=lifespan)
if GALLERY.exists():
    app.mount("/gallery", StaticFiles(directory=GALLERY), name="gallery")


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
    child = ("crib_bassinet_sleeper", "stroller", "high_chair", "play_yard", "toy", "other_child")
    cat_ok = (lambda cat: cat in child) if children_only else (lambda cat: True)

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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "static" / "index.html").read_text()


@app.get("/api/lookup")
def api_lookup(q: str = Query(..., min_length=2, max_length=120), children: bool = False) -> dict[str, Any]:
    return {"query": q, "results": lookup(q, children)}


@app.get("/api/gallery")
def api_gallery() -> list[dict[str, Any]]:
    p = GALLERY / "gallery.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.get("/api/meta")
def api_meta() -> dict[str, str]:
    try:
        return dict(conn().execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as e:
        raise HTTPException(503, str(e)) from e


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": DB.exists()}
