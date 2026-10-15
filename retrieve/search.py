"""Candidate retrieval: image kNN + Gemma read (exact match, brand, text embedding), fused with RRF."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image

from index.build import VectorStore, classify_kinds, image_store_path, text_store_path
from index.models import image_embedder, load_image, text_embedder
from ingest.categories import OUT_OF_SCOPE
from ingest.cpsc import DB_PATH, connect
from read.gemma import ItemRead, OllamaVision, read_item
from retrieve.exact import lookup_brand, lookup_models, lookup_upcs, model_candidates, upc_candidates
from retrieve.fusion import Candidate, boost_exact, rrf

LIST_DEPTH = 50
# RRF weight per signal, chosen by what the photo shows. Tuned on the dev split (eval/tune.py), never on the
# test set: on product photos any weight on the text signal lowered top-3 (Gemma's description of an
# unbranded item is too generic), so text only votes when the photo is mostly a label or packaging,
# where the words are the evidence and the shape is not. SigLIP calls many plain product shots "label" or
# "packaging", so text leads only when Gemma actually read words off the item; with nothing read, a
# text-led ranking pushed the right image match out of the top 3 (eval error analysis, see eval/results.md).
FUSION_WEIGHTS: dict[str, dict[str, float]] = {
    "product": {"image": 1.0, "text": 0.0, "brand": 0.5},
    "label": {"image": 0.25, "text": 1.0, "brand": 0.5},
}

# Which signals to use. The eval ablation toggles these.
MODES = {
    "image": {"image"},
    "text": {"read", "text", "exact", "brand"},
    "fused": {"image", "read", "text", "exact", "brand"},
}


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    read: ItemRead | None = None
    lists: dict[str, list[int]] = field(default_factory=dict)
    query_kind: str = "product"


@lru_cache(maxsize=1)
def _stores() -> tuple[VectorStore, VectorStore | None]:
    img = VectorStore.load(image_store_path())
    tp = text_store_path()
    return img, (VectorStore.load(tp) if tp.exists() else None)


@lru_cache(maxsize=1)
def _out_of_scope_ids(db: str) -> frozenset[int]:
    conn = sqlite3.connect(db)
    q = f"SELECT recall_id FROM recalls WHERE category IN ({','.join('?' * len(OUT_OF_SCOPE))})"
    return frozenset(r[0] for r in conn.execute(q, tuple(OUT_OF_SCOPE)))


def retrieve(
    photo: Image.Image | str,
    mode: str = "fused",
    k: int = 5,
    exclude_keys: Iterable[str] = (),
    read: ItemRead | None = None,
    vision: OllamaVision | None = None,
    db_path: str = str(DB_PATH),
    weights: dict[str, float] | None = None,
) -> RetrievalResult:
    """Return the top-k recall candidates for a photo.

    exclude_keys: image paths to hide from the image index (eval leakage guard).
    read: a precomputed Gemma read, to avoid re-reading the same photo across ablation modes.
    """
    use = MODES[mode]
    img = load_image(photo)
    img_store, text_store = _stores()
    skip = _out_of_scope_ids(db_path)
    excl = list(exclude_keys)
    lists: dict[str, list[int]] = {}
    best_image: dict[int, str] = {}
    image_score: dict[int, float] = {}

    query_kind = {"kind": "product"}

    def image_list() -> list[int]:
        v = image_embedder().embed([img])[0]
        query_kind["kind"] = str(classify_kinds(v)[0])
        mask = ~np.isin(img_store.keys, excl) if excl else None
        hits = [h for h in img_store.search(v, k=LIST_DEPTH + len(skip), mask=mask) if h[0] not in skip]
        for rid, score, key in hits:
            best_image.setdefault(rid, key)
            image_score.setdefault(rid, score)
        return [rid for rid, _, _ in hits][:LIST_DEPTH]

    def do_read() -> ItemRead | None:
        if read is not None or "read" not in use:
            return read
        return read_item(img, vision)

    with ThreadPoolExecutor(2) as ex:
        f_img = ex.submit(image_list) if "image" in use else None
        f_read = ex.submit(do_read)
        if f_img:
            lists["image"] = f_img.result()
        item = f_read.result()

    exact: dict[int, list[str]] = {}
    if item is not None:
        conn = connect(db_path)
        if "exact" in use:
            for rid, why in lookup_models(conn, model_candidates(item.model_number, item.visible_text)).items():
                exact.setdefault(rid, []).extend(why)
            for rid, why in lookup_upcs(conn, upc_candidates(item.upc, item.visible_text)).items():
                exact.setdefault(rid, []).extend(why)
            exact = {r: w for r, w in exact.items() if r not in skip}
        if "text" in use and text_store is not None and item.query_text():
            qv = text_embedder().embed_query(item.query_text())
            lists["text"] = [rid for rid, _, _ in text_store.search(qv, k=LIST_DEPTH + len(skip)) if rid not in skip][
                :LIST_DEPTH
            ]
        if "brand" in use and item.brand:
            brand_ids = set(lookup_brand(db_path, item.brand))
            if brand_ids:
                # Order brand matches by how the other signals ranked them; brand alone is weak evidence.
                order = {rid: i for lst in lists.values() for i, rid in enumerate(lst)}
                lists["brand"] = sorted(brand_ids, key=lambda r: order.get(r, 10_000))[:LIST_DEPTH]
                for rid in brand_ids:
                    exact.setdefault(rid, []).append("brand")

    if weights is None:
        text_led = query_kind["kind"] != "product" and item is not None and item.has_text()
        weights = FUSION_WEIGHTS["label" if text_led else "product"]
        if "image" not in use:  # text-only ablation: the text signal is all there is
            weights = FUSION_WEIGHTS["label"]
    fused = rrf(lists, weights=weights)
    fused = boost_exact(fused, {r: w for r, w in exact.items() if any(x != "brand" for x in w)})
    for c in fused:
        c.best_image = best_image.get(c.recall_id)
        c.image_score = image_score.get(c.recall_id)
        if c.recall_id in exact and "brand" in exact[c.recall_id] and "brand" not in c.exact:
            c.exact.append("brand")
    return RetrievalResult(candidates=fused[:k], read=item, lists=lists, query_kind=query_kind["kind"])
