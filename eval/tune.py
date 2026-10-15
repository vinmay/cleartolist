"""Tune fusion weights on the dev split only (eval/work/dev_queries.json), reusing stored Gemma reads.

    uv run python -m eval.tune
"""

from __future__ import annotations

import itertools
import json

from eval.dataset import WORK
from read.gemma import ItemRead
from retrieve.search import retrieve


def main() -> None:
    qs = {q["path"]: q for q in json.loads((WORK / "dev_queries.json").read_text())}
    rows = [json.loads(line) for line in (WORK / "dev_rows.jsonl").read_text().splitlines()]
    grid = list(itertools.product([1.0], [0.0, 0.1, 0.25, 0.5, 1.0], [0.0, 0.1, 0.25, 0.5]))
    hits = {g: 0 for g in grid}
    for r in rows:
        q = qs[r["path"]]
        read = ItemRead(**r["read"])
        for g in grid:
            w = dict(zip(["image", "text", "brand"], g, strict=True))
            res = retrieve(q["path"], mode="fused", k=3, exclude_keys=q["exclude"], read=read, weights=w)
            hits[g] += q["recall_id"] in [c.recall_id for c in res.candidates]
    for g, h in sorted(hits.items(), key=lambda x: -x[1])[:10]:
        print(f"image={g[0]} text={g[1]} brand={g[2]}: top3 {h}/{len(rows)}")


if __name__ == "__main__":
    main()
