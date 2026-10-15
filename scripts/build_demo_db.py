"""Build the text-only SQLite used by the public Render demo.

No images, no vectors, no raw API payloads: just what lookup needs, plus an FTS5 index.
The nightly GitHub Actions workflow builds this and publishes it as a release asset.

    uv run python scripts/build_demo_db.py [--src data/recalls.db] [--out data/demo.db]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

DEMO_SCHEMA = """
CREATE TABLE recalls (
    recall_id INTEGER PRIMARY KEY, recall_number TEXT, recall_date TEXT, title TEXT, description TEXT,
    url TEXT, hazards TEXT, remedies TEXT, manufacturers TEXT, category TEXT, products TEXT, image_urls TEXT
);
CREATE TABLE model_numbers (recall_id INTEGER, model TEXT);
CREATE TABLE upcs (recall_id INTEGER, upc TEXT);
CREATE INDEX idx_models ON model_numbers(model);
CREATE INDEX idx_upcs ON upcs(upc);
CREATE VIRTUAL TABLE recalls_fts USING fts5(
    title, products, manufacturers, description, content='recalls', content_rowid='recall_id',
    tokenize='porter unicode61'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build(src: Path, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    s = sqlite3.connect(src)
    d = sqlite3.connect(tmp)
    d.executescript(DEMO_SCHEMA)
    n = 0
    for rid, num, date, title, desc, url, hz, rm, mf, cat, raw in s.execute(
        "SELECT recall_id, recall_number, recall_date, title, description, url, hazards, remedies, "
        "manufacturers, category, raw_json FROM recalls WHERE category != 'vehicle'"
    ):
        prods = s.execute("SELECT name, model FROM products WHERE recall_id=?", (rid,)).fetchall()
        products = "; ".join(f"{name} {model}".strip() for name, model in prods)
        image_urls = [i["URL"] for i in (json.loads(raw).get("Images") or []) if i.get("URL")]
        mf_names = ", ".join(json.loads(mf or "[]"))
        d.execute("INSERT INTO recalls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, num, date, title, desc, url, hz, rm, mf, cat, products, json.dumps(image_urls[:4])))
        d.execute("INSERT INTO recalls_fts(rowid, title, products, manufacturers, description) VALUES (?,?,?,?,?)",
                  (rid, title, products, mf_names, desc))
        n += 1
    keep = {r for (r,) in d.execute("SELECT recall_id FROM recalls")}
    d.executemany("INSERT INTO model_numbers VALUES (?,?)",
                  [r for r in s.execute("SELECT recall_id, model FROM model_numbers") if r[0] in keep])
    d.executemany("INSERT INTO upcs VALUES (?,?)",
                  [r for r in s.execute("SELECT recall_id, upc FROM upcs") if r[0] in keep])
    latest = s.execute("SELECT MAX(recall_date) FROM recalls").fetchone()[0]
    d.executemany("INSERT INTO meta VALUES (?,?)", [("recall_count", str(n)), ("latest_recall_date", latest or ""),
                                                   ("source", "U.S. CPSC Recalls API, saferproducts.gov")])
    d.commit()
    d.execute("VACUUM")
    d.close()
    tmp.replace(out)
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path("data/recalls.db"))
    ap.add_argument("--out", type=Path, default=Path("data/demo.db"))
    a = ap.parse_args()
    n = build(a.src, a.out)
    print(f"{n} recalls -> {a.out} ({a.out.stat().st_size / 1e6:.1f} MB)")
