"""End-to-end scan: photo in, verdict JSON out.

    uv run python -m verify.pipeline photo.jpg [photo2.jpg ...]
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from index.models import load_image
from ingest.cpsc import DB_PATH, connect
from read.gemma import ItemRead, OllamaVision
from retrieve.search import retrieve
from verify.gemma import judge, recall_context
from verify.verdict import Verdict, candidate_verdict, overall

DISCLAIMER = ("Screening aid only, not legal advice. 'No match found' means no match in CPSC recalls, "
              "not that the item is safe. Always confirm at cpsc.gov.")
CAR_SEAT_NOTE = ("Car seats are regulated by NHTSA, not CPSC, and are not covered here. "
                 "Check the seat's label at nhtsa.gov/recalls.")


@dataclass
class ScanResult:
    verdict: Verdict
    read: ItemRead | None
    matches: list[dict[str, Any]] = field(default_factory=list)
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
            "timings": self.timings,
            "notes": self.notes + [DISCLAIMER],
        }


def scan(
    photo: Image.Image | str,
    mode: str = "fused",
    verify: bool = True,
    k: int = 5,
    exclude_keys: Iterable[str] = (),
    read: ItemRead | None = None,
    vision: OllamaVision | None = None,
) -> ScanResult:
    vision = vision or OllamaVision()
    img = load_image(photo)
    t0 = time.perf_counter()
    res = retrieve(img, mode=mode, k=k, exclude_keys=exclude_keys, read=read, vision=vision)
    t1 = time.perf_counter()
    conn = connect(DB_PATH)
    matches: list[dict[str, Any]] = []
    for rank, c in enumerate(res.candidates, start=1):
        ctx = recall_context(conn, c.recall_id)
        m = {"rank": rank, "recall_id": c.recall_id, "title": ctx["title"], "url": ctx["url"],
             "hazards": ctx["hazards"], "remedies": ctx["remedies"], "manufacturers": ctx["manufacturers"],
             "recall_image": c.best_image or (ctx["images"][0] if ctx["images"] else None),
             "exact": c.exact, "sources": c.sources, "score": round(c.score, 5)}
        if verify and m["recall_image"]:
            j = judge(vision, img, m["recall_image"], ctx, res.read)
            m["judgment"] = j.__dict__
            m["verdict"] = candidate_verdict(j, c.exact).value
        matches.append(m)
    t2 = time.perf_counter()

    if verify:
        v = overall([Verdict(m["verdict"]) for m in matches if "verdict" in m])
        # Show the strongest match first.
        matches.sort(key=lambda m: (-Verdict(m.get("verdict", "NO MATCH FOUND")).severity, m["rank"]))
    else:
        v = Verdict.UNSURE if matches else Verdict.NO_MATCH
    notes = []
    if v == Verdict.LIKELY:
        notes.append("Photograph the label (model number / date code) and scan again to confirm.")
    if res.read and "car seat" in (res.read.category or "").lower():
        notes.append(CAR_SEAT_NOTE)
    return ScanResult(v, res.read, matches, {"retrieve_s": round(t1 - t0, 2), "verify_s": round(t2 - t1, 2),
                                            "total_s": round(t2 - t0, 2)}, notes)


if __name__ == "__main__":
    vision = OllamaVision()
    for p in sys.argv[1:]:
        r = scan(p, vision=vision)
        print(json.dumps({"photo": p, **r.to_dict()}, indent=1))
