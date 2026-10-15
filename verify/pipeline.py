"""End-to-end scan: photo in, verdict + recall shortlist out.

    uv run python -m verify.pipeline photo.jpg [photo2.jpg ...]

The two-photo flow: scan the item photo -> CONFIRMED, or POSSIBLE MATCH with a top-3 shortlist -> scan a
label photo (label_followup) -> CONFIRMED if a model number or UPC on the label matches a recall.
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
from retrieve.exact import brand_conflict
from retrieve.search import retrieve
from verify.gemma import recall_context
from verify.verdict import Verdict, decide, has_code

SHORTLIST_K = 3
DISCLAIMER = ("Screening aid only, not legal advice. 'No match found' means no match in CPSC recalls, "
              "not that the item is safe. Always confirm at cpsc.gov.")
CAR_SEAT_NOTE = ("Car seats are regulated by NHTSA, not CPSC, and are not covered here. "
                 "Check the seat's label at nhtsa.gov/recalls.")
LABEL_ASK = ("Photograph the label (model number, date code or barcode; look under the seat, on the frame or "
             "on the base) and scan it to check against these recalls.")


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
    matches: list[dict[str, Any]] = field(default_factory=list)  # the shortlist, best first
    text_led: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def top(self) -> dict[str, Any] | None:
        return self.matches[0] if self.matches else None

    def codes_read(self) -> list[str]:
        if not self.read:
            return []
        return [c for c in (self.read.model_number, self.read.upc) if c]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "read": self.read.to_dict() if self.read else None,
            "matches": self.matches,
            "text_led": self.text_led,
            "timings": self.timings,
            "notes": self.notes + [DISCLAIMER],
        }


def scan(
    photo: Image.Image | str,
    exclude_keys: Iterable[str] = (),
    read: ItemRead | None = None,
    vision: OllamaVision | None = None,
    k: int = SHORTLIST_K,
) -> ScanResult:
    vision = vision or OllamaVision()
    img = load_image(photo)
    t0 = time.perf_counter()
    exclude = set(exclude_keys)
    res = retrieve(img, mode="fused", k=k, exclude_keys=exclude, read=read, vision=vision)
    t1 = time.perf_counter()
    conn = connect(DB_PATH)
    matches: list[dict[str, Any]] = []
    for rank, c in enumerate(res.candidates, start=1):
        ctx = recall_context(conn, c.recall_id)
        matches.append({
            "rank": rank, "recall_id": c.recall_id, "title": ctx["title"], "url": ctx["url"],
            "hazards": ctx["hazards"], "remedies": ctx["remedies"], "manufacturers": ctx["manufacturers"],
            "models": ctx["models"],
            "recall_image": pick_recall_image(c.best_image, ctx["images"], exclude),
            "exact": c.exact, "sources": c.sources, "score": round(c.score, 5),
            "image_score": round(c.image_score, 4) if c.image_score is not None else None,
        })
    brand = res.read.brand if res.read else ""
    conflicts = [has_code(m["exact"]) and brand_conflict(str(DB_PATH), brand, m["recall_id"]) for m in matches]
    v, idx = decide([m["exact"] for m in matches], [m["image_score"] for m in matches], res.text_led,
                    brand_conflict=conflicts)
    if v == Verdict.CONFIRMED and idx:
        matches.insert(0, matches.pop(idx))  # the code-matched recall goes first
    if v == Verdict.NO_MATCH:
        matches = []
    notes = []
    if v == Verdict.POSSIBLE:
        notes.append(LABEL_ASK)
    if res.read and "car seat" in (res.read.category or "").lower():
        notes.append(CAR_SEAT_NOTE)
    t2 = time.perf_counter()
    return ScanResult(v, res.read, matches, res.text_led,
                      {"retrieve_s": round(t1 - t0, 2), "total_s": round(t2 - t0, 2)}, notes)


def label_followup(item: dict[str, Any], label: dict[str, Any]) -> dict[str, Any]:
    """Combine an item scan with a scan of its label photo (both as ScanResult.to_dict()).

    Only a model number or UPC on the label that matches a recall confirms; otherwise the item keeps its
    verdict and shortlist, with a note on what the label showed.
    """
    read = label.get("read") or {}
    if label["verdict"] == Verdict.CONFIRMED.value:
        hit = label["matches"][0]
        shortlisted = {m["recall_id"] for m in item.get("matches", [])}
        notes = [] if not shortlisted or hit["recall_id"] in shortlisted else [
            "The label matches a different recall from the photo shortlist; the label is the stronger evidence."]
        return {**label, "step": "label", "notes": notes + [DISCLAIMER]}
    codes = [c for c in (read.get("model_number"), read.get("upc")) if c]
    if codes:
        note = (f"Read {', '.join(codes)} on the label. That isn't listed in any CPSC recall in this database. "
                "Check the recall pages if the item still looks like one of them.")
    else:
        note = ("No model number or UPC could be read on the label. Try a sharper, closer photo, or compare the "
                "label with the recall pages by hand.")
    return {**item, "read": read, "label_read": read, "timings": label["timings"], "step": "label",
            "notes": [note, DISCLAIMER]}


if __name__ == "__main__":
    vision = OllamaVision()
    for p in sys.argv[1:]:
        r = scan(p, vision=vision)
        print(json.dumps({"photo": p, **r.to_dict()}, indent=1))
