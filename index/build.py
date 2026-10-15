"""Build the image and text vector indexes over the recall database.

Vectors are stored as .npz next to the db. At ~15k images a brute-force dot product
is about a millisecond, so no ANN library is needed.

Usage:
    uv run python -m index.build            # image + text
    uv run python -m index.build --image    # image only
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from index.models import DEFAULT_IMAGE_MODEL, DEFAULT_TEXT_MODEL, image_embedder, load_image, text_embedder
from ingest.cpsc import DB_PATH, connect

VEC_DIR = Path("data/vectors")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass
class VectorStore:
    vectors: np.ndarray  # (N, d) float32, L2-normalized
    recall_ids: np.ndarray  # (N,) int
    keys: np.ndarray  # (N,) str — image path or recall id

    @classmethod
    def load(cls, path: Path) -> VectorStore:
        z = np.load(path, allow_pickle=False)
        return cls(z["vectors"].astype(np.float32), z["recall_ids"], z["keys"])

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, vectors=self.vectors.astype(np.float16), recall_ids=self.recall_ids, keys=self.keys)

    def search(self, q: np.ndarray, k: int = 50, mask: np.ndarray | None = None) -> list[tuple[int, float, str]]:
        """Return (recall_id, score, key) for the best row per recall, best first."""
        scores = self.vectors @ q.astype(np.float32)
        if mask is not None:
            scores = np.where(mask, scores, -np.inf)
        order = np.argsort(-scores)
        seen: set[int] = set()
        out: list[tuple[int, float, str]] = []
        for i in order:
            if not np.isfinite(scores[i]):
                break
            rid = int(self.recall_ids[i])
            if rid in seen:
                continue
            seen.add(rid)
            out.append((rid, float(scores[i]), str(self.keys[i])))
            if len(out) >= k:
                break
        return out


def image_store_path(model: str = DEFAULT_IMAGE_MODEL) -> Path:
    return VEC_DIR / f"image_{slug(model)}.npz"


def text_store_path(model: str = DEFAULT_TEXT_MODEL) -> Path:
    return VEC_DIR / f"text_{slug(model)}.npz"


def build_image_index(conn: sqlite3.Connection, model: str = DEFAULT_IMAGE_MODEL, batch: int = 32) -> VectorStore:
    path = image_store_path(model)
    rows = conn.execute("SELECT recall_id, path FROM images WHERE path IS NOT NULL ORDER BY recall_id, path").fetchall()
    old = VectorStore.load(path) if path.exists() else None
    have = {str(k): i for i, k in enumerate(old.keys)} if old else {}
    todo = [(rid, p) for rid, p in rows if p not in have]
    print(f"image index: {len(rows)} images, {len(todo)} to embed", file=sys.stderr)

    vecs: list[np.ndarray] = []
    rids: list[int] = []
    keys: list[str] = []
    if todo:
        emb = image_embedder(model)
        for s in range(0, len(todo), batch):
            chunk = todo[s : s + batch]
            imgs, ok = [], []
            for rid, p in chunk:
                try:
                    imgs.append(load_image(p))
                    ok.append((rid, p))
                except Exception as e:  # corrupt or non-image download
                    print(f"skip {p}: {e}", file=sys.stderr)
            if imgs:
                vecs.append(emb.embed(imgs))
                rids += [r for r, _ in ok]
                keys += [p for _, p in ok]
            if (s // batch) % 20 == 0:
                print(f"  {s + len(chunk)}/{len(todo)}", file=sys.stderr)

    keep = {p for _, p in rows}
    parts_v, parts_r, parts_k = [], [], []
    if old is not None:
        sel = np.array([str(k) in keep for k in old.keys], dtype=bool)
        parts_v.append(old.vectors[sel])
        parts_r.append(old.recall_ids[sel])
        parts_k.append(old.keys[sel])
    if vecs:
        parts_v.append(np.concatenate(vecs))
        parts_r.append(np.array(rids))
        parts_k.append(np.array(keys))
    store = VectorStore(np.concatenate(parts_v), np.concatenate(parts_r), np.concatenate(parts_k))
    store.save(path)
    return store


def recall_text(conn: sqlite3.Connection, recall_id: int) -> tuple[str, str]:
    """(title, body) used for the text embedding of a recall."""
    title, desc, mfrs = conn.execute(
        "SELECT title, description, manufacturers FROM recalls WHERE recall_id=?", (recall_id,)
    ).fetchone()
    prods = conn.execute("SELECT name, description FROM products WHERE recall_id=?", (recall_id,)).fetchall()
    parts = [", ".join(json.loads(mfrs or "[]"))]
    for name, pdesc in prods:
        parts.append(name)
        if pdesc:
            parts.append(pdesc)
    parts.append(desc or "")
    body = ". ".join(p for p in parts if p)
    return title or "none", body[:2000]


def build_text_index(conn: sqlite3.Connection, model: str = DEFAULT_TEXT_MODEL, batch: int = 32) -> VectorStore:
    path = text_store_path(model)
    ids = [r[0] for r in conn.execute("SELECT recall_id FROM recalls ORDER BY recall_id")]
    old = VectorStore.load(path) if path.exists() else None
    have = set(int(r) for r in old.recall_ids) if old else set()
    todo = [i for i in ids if i not in have]
    print(f"text index: {len(ids)} recalls, {len(todo)} to embed", file=sys.stderr)
    emb = text_embedder(model)
    vecs = []
    for s in range(0, len(todo), batch):
        chunk = todo[s : s + batch]
        tb = [recall_text(conn, i) for i in chunk]
        vecs.append(emb.embed_documents([b for _, b in tb], titles=[t for t, _ in tb]))
    parts_v = [old.vectors] if old else []
    parts_r = [old.recall_ids] if old else []
    if vecs:
        parts_v.append(np.concatenate(vecs))
        parts_r.append(np.array(todo))
    r = np.concatenate(parts_r)
    store = VectorStore(np.concatenate(parts_v), r, r.astype(str))
    store.save(path)
    return store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", action="store_true")
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL)
    args = ap.parse_args()
    both = not (args.image or args.text)
    conn = connect(DB_PATH)
    if args.image or both:
        s = build_image_index(conn, args.image_model)
        print(f"image vectors: {s.vectors.shape}", file=sys.stderr)
    if args.text or both:
        s = build_text_index(conn)
        print(f"text vectors: {s.vectors.shape}", file=sys.stderr)


if __name__ == "__main__":
    main()
