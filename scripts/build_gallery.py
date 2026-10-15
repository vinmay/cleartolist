"""Build the Render demo's gallery from scans run locally with the full pipeline.

    uv run python -m scripts.build_gallery --source "Owner photo" demo/photos/*.jpg
    uv run python -m scripts.build_gallery --from-eval 8      # simulated photos from the eval set

Copies each scanned photo (downscaled) into demo/gallery/ and appends an entry to demo/gallery/gallery.json.
The recall photo is linked from cpsc.gov, not copied. Eval-derived entries are labeled as simulated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from pathlib import Path

from index.models import load_image
from ingest.cpsc import DB_PATH
from verify.pipeline import scan

GALLERY = Path("demo/gallery")
INDEX = GALLERY / "gallery.json"


def remote_url(conn: sqlite3.Connection, local_path: str | None) -> str | None:
    if not local_path:
        return None
    row = conn.execute("SELECT url FROM images WHERE path=?", (local_path,)).fetchone()
    return row[0] if row else None


def add(
    photo: str, source: str, exclude: list[str] | None = None, held_out: str | None = None,
    truth: int | None = None,
) -> dict:
    """Scan one photo. Owner photos are copied (downscaled) into the gallery; for eval-derived scans the
    gallery links the original held-out CPSC photo on cpsc.gov instead of redistributing a copy."""
    conn = sqlite3.connect(DB_PATH)
    r = scan(photo, exclude_keys=exclude or []).to_dict()
    name = hashlib.sha1(Path(photo).read_bytes()).hexdigest()[:12] + ".jpg"
    if held_out:
        photo_url = remote_url(conn, held_out)
    else:
        img = load_image(photo)
        img.thumbnail((640, 640))
        GALLERY.mkdir(parents=True, exist_ok=True)
        img.save(GALLERY / name, quality=85)
        photo_url = f"/gallery/{name}"
    flagged = r["verdict"] != "NO MATCH FOUND"
    top = r["matches"][0] if r["matches"] else None
    read = r["read"] or {}
    evidence = ""
    if top and flagged:
        bits = [e for e in top.get("exact", []) if e != "brand"]
        v = r.get("verifier") or {}
        if v.get("index") is not None:
            bits.append(f"Gemma picked this recall ({v.get('confidence')} confidence)")
        bits.append(v.get("reason", ""))
        evidence = "; ".join(b for b in bits if b)
    entry = {
        "photo": name, "photo_url": photo_url, "photo_source": source, "verdict": r["verdict"],
        "recall_title": top["title"] if top and flagged else "",
        "recall_url": top["url"] if top and flagged else "",
        "recall_image_url": remote_url(conn, top["recall_image"]) if top and flagged else "",
        "evidence": evidence,
        "read_summary": ", ".join(x for x in [read.get("category"), read.get("brand"), read.get("model_number")] if x),
        "seconds": r["timings"]["total_s"],
    }
    if truth is not None:  # eval photo: say which recall it really came from and whether the scan got it
        title, url = conn.execute("SELECT title, url FROM recalls WHERE recall_id=?", (truth,)).fetchone()
        right = flagged and top is not None and top["recall_id"] == truth
        entry["truth"] = {"title": title, "url": url,
                          "outcome": "right recall flagged" if right else ("wrong recall" if flagged else "missed")}
    GALLERY.mkdir(parents=True, exist_ok=True)
    items = json.loads(INDEX.read_text()) if INDEX.exists() else []
    items = [i for i in items if i["photo"] != name] + [entry]
    INDEX.write_text(json.dumps(items, indent=1))
    print(f"{photo}: {r['verdict']} {entry['recall_title'][:70]}")
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("photos", nargs="*")
    ap.add_argument("--source", default="Photo scanned locally")
    ap.add_argument("--from-eval", type=int, default=0, help="add N children's eval positives (simulated photos)")
    a = ap.parse_args()
    for p in a.photos:
        add(p, a.source)
    if a.from_eval:
        qs = json.loads(Path("eval/work/queries.json").read_text())
        qs = [q for q in qs if q["run"] == "children" and q["label"] == "pos"]
        for q in random.Random(3).sample(qs, a.from_eval):
            add(q["path"], "Simulated: a held-out CPSC recall photo (shown) was cropped, rotated, dimmed and "
                "blurred, then scanned with that photo hidden from the index. Not a real thrift photo.",
                q["exclude"], q["held_out"], q["recall_id"])


if __name__ == "__main__":
    main()
