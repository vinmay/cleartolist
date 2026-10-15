"""Feasibility spike: image-embedding kNN only, top-k on held-out positives per run and category.

    uv run python -m eval.spike [--model google/siglip2-base-patch16-224]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

import numpy as np

from eval.dataset import WORK
from index.build import VectorStore, image_store_path
from index.models import DEFAULT_IMAGE_MODEL, image_embedder, load_image


def fmt(r: list[int]) -> str:
    a = np.array(r)
    return f"n={len(a):3d}  top1={np.mean(a <= 1):6.1%}  top3={np.mean(a <= 3):6.1%}  top10={np.mean(a <= 10):6.1%}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_IMAGE_MODEL)
    a = ap.parse_args()
    store = VectorStore.load(image_store_path(a.model))
    emb = image_embedder(a.model)
    qs = [q for q in json.loads((WORK / "queries.json").read_text()) if q["label"] == "pos"]
    by_run: dict[str, list[int]] = defaultdict(list)
    by_cat: dict[tuple[str, str], list[int]] = defaultdict(list)
    for q in qs:
        v = emb.embed([load_image(q["path"])])[0]
        mask = ~np.isin(store.keys, q["exclude"])
        hits = [rid for rid, _, _ in store.search(v, k=10, mask=mask)]
        rank = hits.index(q["recall_id"]) + 1 if q["recall_id"] in hits else 99
        by_run[q["run"]].append(rank)
        by_cat[(q["run"], q["category"])].append(rank)
    print(f"model={a.model}")
    for run, r in by_run.items():
        print(f"{run:9s} ALL                    {fmt(r)}")
        for (rn, cat), rc in sorted(by_cat.items()):
            if rn == run:
                print(f"{'':9s} {cat:22s} {fmt(rc)}")


if __name__ == "__main__":
    main()
