"""Build the evaluation query sets.

Two runs, built the same way:
  * children  — headline run: cribs/bassinets/sleepers, strollers, high chairs, play yards, toys, other children's
  * mixed     — other CPSC categories (furniture, heaters, kitchen, chargers, tools, sports, home ...)

Positives: a recall with 2+ *distinct* images. One image is held out as the query and a seeded,
realistic degradation is applied (crop, cluttered background, rotation, blur, dim light, JPEG).
Leakage guard: the held-out image and every copy of it — byte-identical files or near-duplicates
(SigLIP cosine >= DUP_COS, e.g. the same photo resized under another URL, in any recall) — are
listed in `exclude` and masked out of every index at search time. The recall must still have
at least one non-excluded image, so a hit can only come from a *different* photo.

Negatives: same-category lookalikes from eval/negatives_manifest.json (licensed, see
eval/negatives.py), plus owner photos in eval/owner_photos/ (name recall lookalikes
<recall_id>_anything.jpg; anything else counts as a negative).

Car seats (NHTSA, not CPSC) and motor vehicles (out of scope) are excluded.
Writes eval/work/queries.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from index.build import VectorStore, image_kinds, image_store_path
from index.models import load_image
from ingest.categories import CHILD_CATEGORIES
from ingest.cpsc import DB_PATH, connect

WORK = Path("eval/work")
NEG_DIR = Path("data/negatives")
NEG_MANIFEST = Path("eval/negatives_manifest.json")
NEG_DEV_MANIFEST = Path("eval/negatives_dev_manifest.json")
OWNER_DIR = Path("eval/owner_photos")
DUP_COS = 0.95

MIXED_CATEGORIES = {"furniture", "heating_cooling", "kitchen", "electrical_battery", "tools_outdoor",
                    "sports_rec", "home", "apparel", "appliance_fixture"}


def run_of(category: str) -> str | None:
    if category in CHILD_CATEGORIES:
        return "children"
    if category in MIXED_CATEGORIES:
        return "mixed"
    return None


def degrade(img: Image.Image, rng: random.Random) -> Image.Image:
    """Crop, rotate, blur, dim and drop onto a cluttered background, like a phone photo of a haul."""
    w, h = img.size
    cw, ch = int(w * rng.uniform(0.7, 0.95)), int(h * rng.uniform(0.7, 0.95))
    x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
    img = img.crop((x, y, x + cw, y + ch))
    scale = rng.uniform(0.6, 0.85)
    bw, bh = int(cw / scale), int(ch / scale)
    bg = Image.new("RGB", (bw, bh), tuple(rng.randint(60, 200) for _ in range(3)))
    for _ in range(12):
        bx, by = rng.randint(0, bw), rng.randint(0, bh)
        bg.paste(tuple(rng.randint(0, 255) for _ in range(3)),
                 (bx, by, bx + rng.randint(20, bw // 2 + 21), by + rng.randint(20, bh // 2 + 21)))
    bg = bg.filter(ImageFilter.GaussianBlur(6))
    bg.paste(img, (rng.randint(0, bw - cw), rng.randint(0, bh - ch)))
    img = bg.rotate(rng.uniform(-15, 15), expand=False, fillcolor=(90, 90, 90), resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.8)))
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.55, 0.9))
    img = ImageEnhance.Color(img).enhance(rng.uniform(0.7, 1.1))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=rng.randint(55, 80))
    return Image.open(BytesIO(buf.getvalue())).convert("RGB")


def _sha(path: str) -> str:
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()


def leakage_exclusions(store: VectorStore, held: str, hashes: dict[str, str]) -> list[str]:
    """Every indexed image that is the held-out photo or a copy of it."""
    i = int(np.where(store.keys == held)[0][0])
    sims = store.vectors @ store.vectors[i]
    near = {str(k) for k in store.keys[sims >= DUP_COS]}
    same = {k for k, h in hashes.items() if h == hashes[held]}
    return sorted(near | same | {held})


def build(per_category: int = 20, seed: int = 7, per_child_category: int = 40, out: str = "queries.json",
          avoid: set[int] | None = None, negatives: bool = True, label_queries: int = 40,
          neg_manifest: Path = NEG_MANIFEST, owner: bool = True, avoid_photos: set[str] | None = None) -> list[dict]:
    avoid = avoid or set()
    avoid_photos = avoid_photos or set()  # e.g. every photo the test set hides, so dev never sees a test photo
    conn = connect(DB_PATH)
    store = VectorStore.load(image_store_path())
    kind = dict(zip((str(k) for k in store.keys), image_kinds(store), strict=True))
    indexed = {str(k) for k in store.keys}
    by_recall: dict[int, list[str]] = defaultdict(list)
    for rid, p in conn.execute("SELECT recall_id, path FROM images WHERE path IS NOT NULL ORDER BY path"):
        if p in indexed:
            by_recall[rid].append(p)
    cats = dict(conn.execute("SELECT recall_id, category FROM recalls"))
    hashes = {p: _sha(p) for ps in by_recall.values() for p in ps}

    rng = random.Random(seed)
    pools: dict[str, list[int]] = defaultdict(list)
    for rid, ps in by_recall.items():
        if rid not in avoid and run_of(cats.get(rid, "")) and len({hashes[p] for p in ps}) >= 2:
            pools[cats[rid]].append(rid)

    (WORK / "pos").mkdir(parents=True, exist_ok=True)
    queries: list[dict] = []
    for cat in sorted(pools):
        pool = sorted(pools[cat])
        rng.shuffle(pool)
        taken = 0
        for rid in pool:
            if taken >= (per_child_category if run_of(cat) == "children" else per_category):
                break
            # A reseller photographs the item itself, so the query is a product shot (label close-ups
            # are the follow-up "photograph the label" step, handled by Gemma reading + exact match).
            products = [p for p in by_recall[rid] if kind[p] == "product"]
            if not products:
                continue
            held = products[0]
            excl = leakage_exclusions(store, held, hashes)
            if set(excl) & avoid_photos:
                continue
            if not [p for p in products if p not in set(excl)]:
                continue  # no other distinct product photo of this recall to match against
            qpath = WORK / "pos" / f"{rid}.jpg"
            degrade(load_image(held), rng).save(qpath, quality=90)
            queries.append({"path": str(qpath), "label": "pos", "recall_id": rid, "category": cat,
                            "run": run_of(cat), "held_out": held, "exclude": excl, "source": "cpsc_holdout"})
            taken += 1

    # Label run: the follow-up "photograph the label" step. Query = a held-out label/tag close-up of a
    # children's product; nothing but the words on it can identify the recall.
    if label_queries:
        label_pool = sorted(rid for cat in pools if run_of(cat) == "children" for rid in pools[cat]
                            if rid not in {q["recall_id"] for q in queries})
        rng.shuffle(label_pool)
        taken = 0
        for rid in label_pool:
            if taken >= label_queries:
                break
            labels = [p for p in by_recall[rid] if kind[p] == "label"]
            if not labels:
                continue
            held = labels[0]
            excl = leakage_exclusions(store, held, hashes)
            if set(excl) & avoid_photos:
                continue
            if not [p for p in by_recall[rid] if p not in set(excl)]:
                continue
            qpath = WORK / "pos" / f"{rid}_label.jpg"
            degrade(load_image(held), rng).save(qpath, quality=90)
            queries.append({"path": str(qpath), "label": "pos", "recall_id": rid, "category": cats[rid],
                            "run": "label", "held_out": held, "exclude": excl, "source": "cpsc_holdout_label"})
            taken += 1

    if negatives and neg_manifest.exists():
        for item in json.loads(neg_manifest.read_text())["items"]:
            p = NEG_DIR / item["file"]
            if p.exists() and run_of(item["category"]) and not item.get("excluded"):
                queries.append({"path": str(p), "label": "neg", "recall_id": None, "category": item["category"],
                                "run": run_of(item["category"]), "exclude": [], "source": "commons"})
    for p in sorted(OWNER_DIR.glob("*")) if owner and OWNER_DIR.exists() else []:
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        head = p.stem.split("_")[0]
        rid = int(head) if head.isdigit() else None
        cat = cats.get(rid, "owner") if rid else "owner"
        queries.append({"path": str(p), "label": "pos" if rid else "neg", "recall_id": rid, "category": cat,
                        "run": "owner", "exclude": [], "source": "owner"})
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / out).write_text(json.dumps(queries, indent=1))
    return queries


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=20)
    ap.add_argument("--per-child-category", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--dev", action="store_true",
                    help="build the tuning split (dev_queries.json) from recalls not in the test set")
    a = ap.parse_args()
    if a.dev:
        test = json.loads((WORK / "queries.json").read_text())
        test_ids = {x["recall_id"] for x in test if x["recall_id"]}
        test_photos = {e for x in test for e in x["exclude"]}
        q = build(8, 1234, 25, out="dev_queries.json", avoid=test_ids, label_queries=25,
                  neg_manifest=NEG_DEV_MANIFEST, owner=False, avoid_photos=test_photos)
    else:
        q = build(a.per_category, a.seed, a.per_child_category)
    for run in ("children", "mixed", "label", "owner"):
        pos = sum(x["label"] == "pos" and x["run"] == run for x in q)
        neg = sum(x["label"] == "neg" and x["run"] == run for x in q)
        print(f"{run}: {pos} positives, {neg} negatives")
