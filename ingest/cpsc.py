"""Fetch CPSC recalls, normalize them, store in SQLite and download images.

Usage:
    uv run python -m ingest.cpsc --days 90
    uv run python -m ingest.cpsc --since 2015-01-01
    uv run python -m ingest.cpsc --incremental   # uses last LastPublishDate seen
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

import httpx

from ingest.categories import categorize

API = "https://www.saferproducts.gov/RestWebServices/Recall"
DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "recalls.db"
IMG_DIR = DATA_DIR / "images"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recalls (
    recall_id INTEGER PRIMARY KEY,
    recall_number TEXT,
    recall_date TEXT,
    last_publish_date TEXT,
    title TEXT,
    description TEXT,
    url TEXT,
    hazards TEXT,
    remedies TEXT,
    manufacturers TEXT,
    raw_json TEXT,
    category TEXT
);
CREATE TABLE IF NOT EXISTS products (
    recall_id INTEGER,
    name TEXT,
    description TEXT,
    model TEXT,
    type TEXT,
    number_of_units TEXT
);
CREATE TABLE IF NOT EXISTS images (
    recall_id INTEGER,
    url TEXT,
    path TEXT,
    PRIMARY KEY (recall_id, url)
);
CREATE TABLE IF NOT EXISTS upcs (recall_id INTEGER, upc TEXT);
CREATE TABLE IF NOT EXISTS model_numbers (recall_id INTEGER, model TEXT);
CREATE INDEX IF NOT EXISTS idx_products_recall ON products(recall_id);
CREATE INDEX IF NOT EXISTS idx_upcs ON upcs(upc);
CREATE INDEX IF NOT EXISTS idx_models ON model_numbers(model);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(recalls)")}
    if "category" not in cols:  # databases created before categories existed
        conn.execute("ALTER TABLE recalls ADD COLUMN category TEXT")
    return conn


# ---------- normalization ----------

_MODEL_TOKEN = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{2,}\b|\b\d{4,}\b")
_MODEL_CONTEXT = re.compile(
    r"(?:model|item|style|sku|product id|part)\s*(?:numbers?|nos?\.?|#)?\s*[:#]?\s*([^.;]{0,400})",
    re.IGNORECASE,
)


def normalize_model(s: str) -> str:
    """Canonical form for model-number matching: uppercase alphanumerics only."""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def normalize_upc(s: str) -> str:
    digits = re.sub(r"\D", "", s)
    # UPC-A is 12 digits; EAN-13 with a leading 0 is the same code.
    if len(digits) == 13 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def normalize_manufacturer(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(inc|llc|ltd|co|corp|corporation|company|limited|usa|us|of|the)\b", " ", s)
    return " ".join(s.split())


def extract_model_numbers(*texts: str) -> set[str]:
    """Pull plausible model numbers from free text (Product.Model and descriptions)."""
    out: set[str] = set()
    for text in texts:
        if not text:
            continue
        chunks = [m.group(1) for m in _MODEL_CONTEXT.finditer(text)]
        for chunk in chunks:
            for tok in _MODEL_TOKEN.findall(chunk.upper()):
                n = normalize_model(tok)
                if 4 <= len(n) <= 20 and not re.fullmatch(r"(19|20)\d\d", n):
                    out.add(n)
    return out


def _names(items: list[dict[str, Any]] | None, key: str = "Name") -> list[str]:
    return [i[key].strip() for i in (items or []) if i.get(key)]


def normalize(rec: dict[str, Any]) -> dict[str, Any]:
    products = [
        {
            "name": (p.get("Name") or "").strip(),
            "description": (p.get("Description") or "").strip(),
            "model": (p.get("Model") or "").strip(),
            "type": (p.get("Type") or "").strip(),
            "number_of_units": (p.get("NumberOfUnits") or "").strip(),
        }
        for p in rec.get("Products") or []
    ]
    models: set[str] = set()
    for p in products:
        if p["model"]:
            # The Model field is usually a list of model numbers separated by commas/semicolons.
            for tok in re.split(r"[,;/]| and ", p["model"]):
                n = normalize_model(tok)
                if 3 <= len(n) <= 20:
                    models.add(n)
    models |= extract_model_numbers(rec.get("Description") or "", *(p["description"] for p in products))
    upcs = {normalize_upc(u.get("UPC") or "") for u in rec.get("ProductUPCs") or []}
    upcs = {u for u in upcs if len(u) >= 8}
    return {
        "recall_id": rec["RecallID"],
        "recall_number": rec.get("RecallNumber"),
        "recall_date": (rec.get("RecallDate") or "")[:10],
        "last_publish_date": (rec.get("LastPublishDate") or "")[:10],
        "title": (rec.get("Title") or "").strip(),
        "description": (rec.get("Description") or "").strip(),
        "url": rec.get("URL"),
        "hazards": _names(rec.get("Hazards")),
        "remedies": _names(rec.get("Remedies")),
        "manufacturers": _names(rec.get("Manufacturers")),
        "products": products,
        "images": [i["URL"] for i in rec.get("Images") or [] if i.get("URL")],
        "upcs": sorted(upcs),
        "models": sorted(models),
        "category": categorize(
            rec.get("Title") or "", [p["name"] for p in products], [p["type"] for p in products]
        ),
        "raw": rec,
    }


# ---------- fetching ----------


def fetch_window(client: httpx.Client, start: date, end: date, field: str = "RecallDate") -> list[dict]:
    params = {"format": "json", f"{field}Start": start.isoformat(), f"{field}End": end.isoformat()}
    r = client.get(API, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def windows(start: date, end: date, days: int = 90) -> Iterator[tuple[date, date]]:
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


def save(conn: sqlite3.Connection, n: dict[str, Any]) -> None:
    rid = n["recall_id"]
    conn.execute(
        "INSERT OR REPLACE INTO recalls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rid, n["recall_number"], n["recall_date"], n["last_publish_date"], n["title"],
            n["description"], n["url"], json.dumps(n["hazards"]), json.dumps(n["remedies"]),
            json.dumps(n["manufacturers"]), json.dumps(n["raw"]), n["category"],
        ),
    )
    for table in ("products", "upcs", "model_numbers"):
        conn.execute(f"DELETE FROM {table} WHERE recall_id=?", (rid,))
    conn.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?,?)",
        [(rid, p["name"], p["description"], p["model"], p["type"], p["number_of_units"]) for p in n["products"]],
    )
    conn.executemany("INSERT INTO upcs VALUES (?,?)", [(rid, u) for u in n["upcs"]])
    conn.executemany("INSERT INTO model_numbers VALUES (?,?)", [(rid, m) for m in n["models"]])
    for url in n["images"]:
        conn.execute("INSERT OR IGNORE INTO images (recall_id, url, path) VALUES (?,?,NULL)", (rid, url))


def image_path(recall_id: int, url: str) -> Path:
    ext = Path(url.split("?")[0]).suffix.lower() or ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"
    h = hashlib.sha1(url.encode()).hexdigest()[:12]
    return IMG_DIR / str(recall_id) / f"{h}{ext}"


def download_images(conn: sqlite3.Connection, workers: int = 8) -> int:
    rows = conn.execute("SELECT recall_id, url FROM images WHERE path IS NULL").fetchall()
    if not rows:
        return 0

    def one(row: tuple[int, str]) -> tuple[int, str, str | None]:
        rid, url = row
        dest = image_path(rid, url)
        if dest.exists():
            return rid, url, str(dest)
        try:
            with httpx.Client(follow_redirects=True, timeout=60) as c:
                r = c.get(url)
                r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image"):
                return rid, url, None
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return rid, url, str(dest)
        except httpx.HTTPError:
            return rid, url, None

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for rid, url, path in ex.map(one, rows):
            if path:
                conn.execute("UPDATE images SET path=? WHERE recall_id=? AND url=?", (path, rid, url))
                done += 1
    conn.commit()
    return done


def ingest(start: date, end: date, field: str = "RecallDate", images: bool = True) -> None:
    conn = connect()
    total = 0
    with httpx.Client(follow_redirects=True) as client:
        for a, b in windows(start, end):
            recs = fetch_window(client, a, b, field)
            for rec in recs:
                save(conn, normalize(rec))
            conn.commit()
            total += len(recs)
            print(f"{a}..{b}: {len(recs)} recalls", file=sys.stderr)
    print(f"fetched {total} recalls", file=sys.stderr)
    if images:
        n = download_images(conn)
        print(f"downloaded {n} images", file=sys.stderr)


def recategorize(conn: sqlite3.Connection) -> None:
    """Re-run the categorizer over stored recalls (after editing the keyword rules)."""
    for rid, raw in conn.execute("SELECT recall_id, raw_json FROM recalls").fetchall():
        conn.execute("UPDATE recalls SET category=? WHERE recall_id=?", (normalize(json.loads(raw))["category"], rid))
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--days", type=int, help="recalls from the last N days")
    g.add_argument("--since", type=date.fromisoformat, help="recalls since this date (YYYY-MM-DD)")
    g.add_argument("--incremental", action="store_true", help="recalls published since the newest one stored")
    g.add_argument("--recategorize", action="store_true", help="recompute categories only")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()
    if args.recategorize:
        recategorize(connect())
        return
    today = date.today()
    if args.incremental:
        conn = connect()
        last = conn.execute("SELECT MAX(last_publish_date) FROM recalls").fetchone()[0]
        start = date.fromisoformat(last) - timedelta(days=1) if last else date(2015, 1, 1)
        ingest(start, today, field="LastPublishDate", images=not args.no_images)
    else:
        start = args.since or today - timedelta(days=args.days or 90)
        ingest(start, today, images=not args.no_images)


if __name__ == "__main__":
    main()
