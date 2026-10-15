"""Build the Render demo's gallery from scans run locally with the full pipeline.

    uv run python -m scripts.build_gallery --pairs 4        # two-photo flow: item photo, then its label
    uv run python -m scripts.build_gallery --from-eval 6    # single item photos from the eval set
    uv run python -m scripts.build_gallery --source "Owner photo" demo/photos/*.jpg

Simulated entries use held-out CPSC photos (degraded like phone shots) that are hidden from the index for that
scan; the gallery links the original photos on cpsc.gov rather than redistributing copies, and every card says
which recall the photo really came from. Owner photos are copied (downscaled) into demo/gallery/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from pathlib import Path
from typing import Any

from eval.dataset import _sha, degrade, leakage_exclusions
from index.build import VectorStore, image_kinds, image_store_path
from index.models import load_image
from ingest.categories import CHILD_CATEGORIES
from ingest.cpsc import DB_PATH
from verify.pipeline import label_followup, scan

GALLERY = Path("demo/gallery")
INDEX = GALLERY / "gallery.json"
WORK = Path("eval/work/gallery")


def remote_url(conn: sqlite3.Connection, local_path: str | None) -> str | None:
    if not local_path:
        return None
    row = conn.execute("SELECT url FROM images WHERE path=?", (local_path,)).fetchone()
    return row[0] if row else None


def card(conn: sqlite3.Connection, r: dict[str, Any]) -> dict[str, Any]:
    ms = r["matches"][:1] if r["verdict"] == "CONFIRMED" else r["matches"]
    read = r.get("read") or {}
    return {
        "verdict": r["verdict"],
        "recalls": [{"title": m["title"], "url": m["url"], "image_url": remote_url(conn, m["recall_image"]),
                     "code": ", ".join(e for e in m["exact"] if e.startswith(("model:", "upc:")))} for m in ms],
        "read_summary": ", ".join(x for x in [read.get("category"), read.get("brand"), read.get("model_number"),
                                              read.get("upc")] if x),
        "seconds": r["timings"]["total_s"],
        "note": next((n for n in r["notes"] if not n.startswith(("Screening", "Photograph the label"))), ""),
    }


def truth_of(conn: sqlite3.Connection, rid: int, final: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
    title, url = conn.execute("SELECT title, url FROM recalls WHERE recall_id=?", (rid,)).fetchone()
    ids = [m["recall_id"] for m in r["matches"]]
    if final["verdict"] == "CONFIRMED":
        outcome = "confirmed the right recall" if ids[:1] == [rid] else "confirmed a different recall"
    elif final["verdict"] == "POSSIBLE MATCH":
        outcome = "right recall in the shortlist" if rid in ids else "right recall not in the shortlist"
    else:
        outcome = "missed"
    return {"title": title, "url": url, "outcome": outcome}


def save(entries: list[dict[str, Any]]) -> None:
    GALLERY.mkdir(parents=True, exist_ok=True)
    items = json.loads(INDEX.read_text()) if INDEX.exists() else []
    keys = {e["id"] for e in entries}
    INDEX.write_text(json.dumps([i for i in items if i.get("id") not in keys] + entries, indent=1))


SIMULATED = ("Simulated: held-out CPSC photos (linked) were cropped, rotated, dimmed and blurred, then scanned "
             "with those photos hidden from the index. Not a real thrift photo.")


def pairs(n: int, seed: int = 11) -> list[dict[str, Any]]:
    """Children's recalls with a product photo and a label photo, both held out: the two-photo flow."""
    conn = sqlite3.connect(DB_PATH)
    store = VectorStore.load(image_store_path())
    kind = dict(zip((str(k) for k in store.keys), image_kinds(store), strict=True))
    test = {q["recall_id"] for f in ("queries.json", "dev_queries.json")
            for q in json.loads((Path("eval/work") / f).read_text()) if q["recall_id"]}
    cats = dict(conn.execute("SELECT recall_id, category FROM recalls"))
    by: dict[int, list[str]] = {}
    for rid, p in conn.execute("SELECT recall_id, path FROM images WHERE path IS NOT NULL ORDER BY path"):
        if p in kind:
            by.setdefault(rid, []).append(p)
    hashes = {p: _sha(p) for ps in by.values() for p in ps}
    pool = sorted(rid for rid, ps in by.items() if cats.get(rid) in CHILD_CATEGORIES and rid not in test
                  and sum(kind[p] == "product" for p in ps) >= 2 and any(kind[p] == "label" for p in ps))
    rng = random.Random(seed)
    rng.shuffle(pool)
    WORK.mkdir(parents=True, exist_ok=True)
    out = []
    for rid in pool[:n]:
        prod = next(p for p in by[rid] if kind[p] == "product")
        lab = next(p for p in by[rid] if kind[p] == "label")
        excl = sorted(set(leakage_exclusions(store, prod, hashes)) | set(leakage_exclusions(store, lab, hashes)))
        ip, lp = WORK / f"{rid}_item.jpg", WORK / f"{rid}_label.jpg"
        degrade(load_image(prod), rng).save(ip, quality=90)
        degrade(load_image(lab), rng).save(lp, quality=90)
        item = scan(str(ip), exclude_keys=excl).to_dict()
        final = label_followup(item, scan(str(lp), exclude_keys=excl).to_dict())
        e = {"id": f"pair-{rid}", "kind": "pair", "photo_url": remote_url(conn, prod),
             "label_photo_url": remote_url(conn, lab), "photo_source": SIMULATED,
             "item": card(conn, item), "final": card(conn, final), "truth": truth_of(conn, rid, final, final)}
        print(f"pair {rid}: item {item['verdict']} -> label {final['verdict']} ({e['truth']['outcome']})")
        out.append(e)
    return out


def from_eval(n: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    qs = [q for q in json.loads(Path("eval/work/queries.json").read_text())
          if q["run"] == "children" and q["label"] == "pos"]
    out = []
    for q in random.Random(3).sample(qs, n):
        r = scan(q["path"], exclude_keys=q["exclude"]).to_dict()
        e = {"id": f"single-{q['recall_id']}", "kind": "single", "photo_url": remote_url(conn, q["held_out"]),
             "photo_source": SIMULATED, "item": card(conn, r), "truth": truth_of(conn, q["recall_id"], r, r)}
        print(f"single {q['recall_id']}: {r['verdict']} ({e['truth']['outcome']})")
        out.append(e)
    return out


def owner(photos: list[str], source: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    out = []
    for p in photos:
        r = scan(p).to_dict()
        name = hashlib.sha1(Path(p).read_bytes()).hexdigest()[:12] + ".jpg"
        img = load_image(p)
        img.thumbnail((640, 640))
        GALLERY.mkdir(parents=True, exist_ok=True)
        img.save(GALLERY / name, quality=85)
        out.append({"id": f"owner-{name}", "kind": "single", "photo_url": f"/gallery/{name}", "photo_source": source,
                    "item": card(conn, r)})
        print(f"{p}: {r['verdict']}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("photos", nargs="*")
    ap.add_argument("--source", default="Photo scanned locally")
    ap.add_argument("--pairs", type=int, default=0)
    ap.add_argument("--from-eval", type=int, default=0, help="add N children's eval positives (simulated photos)")
    a = ap.parse_args()
    entries = owner(a.photos, a.source) + pairs(a.pairs) + from_eval(a.from_eval)
    save(entries)


if __name__ == "__main__":
    main()
