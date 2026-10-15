"""Compare Gemma 4 variants as the reader: does the read map to the recall's product category?

    uv run python -m eval.model_compare --models gemma4:e2b,gemma4:e4b --n 30
"""

from __future__ import annotations

import argparse
import json
import random
import time

from eval.dataset import WORK
from ingest.categories import categorize
from read.gemma import OllamaVision, read_item


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="gemma4:e2b,gemma4:e4b")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--run", default="children")
    a = ap.parse_args()
    qs = [q for q in json.loads((WORK / "queries.json").read_text()) if q["label"] == "pos" and q["run"] == a.run]
    qs = random.Random(1).sample(qs, min(a.n, len(qs)))
    for model in a.models.split(","):
        v = OllamaVision(model)
        read_item(qs[0]["path"], v)  # warm-up / load
        ok, child_ok, secs = 0, 0, []
        for q in qs:
            t = time.perf_counter()
            r = read_item(q["path"], v)
            secs.append(time.perf_counter() - t)
            cat = categorize(r.category, [r.description])
            ok += cat == q["category"]
            child_ok += r.child_product == (q["run"] == "children")
        secs.sort()
        print(f"{model}: category agreement {ok}/{len(qs)}, child flag {child_ok}/{len(qs)}, "
              f"median {secs[len(secs) // 2]:.1f}s")


if __name__ == "__main__":
    main()
