"""End-to-end scan: photo in, verdict JSON out.

    uv run python -m verify.pipeline photo.jpg [photo2.jpg ...]
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image

from index.build import VectorStore, image_kinds, image_store_path
from index.models import load_image
from ingest.cpsc import DB_PATH, connect
from read.gemma import ItemRead, OllamaVision
from retrieve.search import retrieve
from verify.gemma import choose, recall_context
from verify.verdict import Choice, Verdict, decide

DISCLAIMER = ("Screening aid only, not legal advice. 'No match found' means no match in CPSC recalls, "
              "not that the item is safe. Always confirm at cpsc.gov.")
CAR_SEAT_NOTE = ("Car seats are regulated by NHTSA, not CPSC, and are not covered here. "
                 "Check the seat's label at nhtsa.gov/recalls.")


@lru_cache(maxsize=1)
def _kinds() -> dict[str, str]:
    store = VectorStore.load(image_store_path())
    return dict(zip((str(k) for k in store.keys), image_kinds(store), strict=True))


def pick_recall_image(best: str | None, images: list[str], exclude: set[str]) -> str | None:
    """The image-search hit if there is one, else the recall's first product photo (not a label shot)."""
    if best:
        return best
    usable = [p for p in images if p not in exclude]
    kinds = _kinds()
    products = [p for p in usable if kinds.get(p) == "product"]
    return (products or usable or [None])[0]


@dataclass
class ScanResult:
    verdict: Verdict
    read: ItemRead | None
    matches: list[dict[str, Any]] = field(default_factory=list)
    choice: Choice | None = None
    timings: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def top(self) -> dict[str, Any] | None:
        return self.matches[0] if self.matches else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "read": self.read.to_dict() if self.read else None,
            "matches": self.matches,
            "verifier": self.choice.__dict__ if self.choice else None,
            "timings": self.timings,
            "notes": self.notes + [DISCLAIMER],
        }


def scan(
    photo: Image.Image | str,
    mode: str = "fused",
    verify: bool = True,
    k: int = 3,
    exclude_keys: Iterable[str] = (),
    read: ItemRead | None = None,
    vision: OllamaVision | None = None,
) -> ScanResult:
    vision = vision or OllamaVision()
    img = load_image(photo)
    t0 = time.perf_counter()
    exclude = set(exclude_keys)
    res = retrieve(img, mode=mode, k=k, exclude_keys=exclude, read=read, vision=vision)
    t1 = time.perf_counter()
    conn = connect(DB_PATH)
    matches: list[dict[str, Any]] = []
    for rank, c in enumerate(res.candidates, start=1):
        ctx = recall_context(conn, c.recall_id)
        matches.append({
            "rank": rank, "recall_id": c.recall_id, "title": ctx["title"], "url": ctx["url"],
            "hazards": ctx["hazards"], "remedies": ctx["remedies"], "manufacturers": ctx["manufacturers"],
            "recall_image": pick_recall_image(c.best_image, ctx["images"], exclude),
            "exact": c.exact, "sources": c.sources, "score": round(c.score, 5),
            "image_score": round(c.image_score, 4) if c.image_score is not None else None, "_ctx": ctx,
        })
    choice = None
    usable = [m for m in matches if m["recall_image"]]
    if verify and usable:
        ans = choose(vision, img, [(m["recall_image"], m["_ctx"]) for m in usable], res.read)
        choice = Choice(ans["index"], ans.get("confidence", "low"), ans.get("same_brand", "cannot_tell"),
                        ans.get("model_consistent", "cannot_tell"), ans.get("reason", ""))
        v, idx = decide(choice, [m["exact"] for m in usable], [m["image_score"] for m in usable])
        chosen = usable[idx]["recall_id"] if idx is not None else None
    else:
        v, chosen = (Verdict.UNSURE if matches else Verdict.NO_MATCH), None
    for m in matches:
        m.pop("_ctx")
        m["verdict"] = v.value if m["recall_id"] == chosen else Verdict.NO_MATCH.value
    # The recall the verdict refers to goes first.
    matches.sort(key=lambda m: (m["recall_id"] != chosen, m["rank"]))
    t2 = time.perf_counter()

    notes = []
    if v == Verdict.LIKELY:
        notes.append("Photograph the label (model number / date code) and scan again to confirm.")
    if res.read and "car seat" in (res.read.category or "").lower():
        notes.append(CAR_SEAT_NOTE)
    return ScanResult(v, res.read, matches, choice, {"retrieve_s": round(t1 - t0, 2), "verify_s": round(t2 - t1, 2),
                                            "total_s": round(t2 - t0, 2)}, notes)


if __name__ == "__main__":
    vision = OllamaVision()
    for p in sys.argv[1:]:
        r = scan(p, vision=vision)
        print(json.dumps({"photo": p, **r.to_dict()}, indent=1))
