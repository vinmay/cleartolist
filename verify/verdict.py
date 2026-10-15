"""Map verifier judgments + exact-match evidence to a verdict. Pure logic, unit-tested."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    CONFIRMED = "CONFIRMED"
    LIKELY = "LIKELY"
    UNSURE = "UNSURE"
    NO_MATCH = "NO MATCH FOUND"

    @property
    def severity(self) -> int:
        return {"CONFIRMED": 3, "LIKELY": 2, "UNSURE": 1, "NO MATCH FOUND": 0}[self.value]


@dataclass
class Judgment:
    same_product_type: bool
    same_brand: str  # "yes" | "no" | "cannot_tell"
    model_consistent: str  # "yes" | "no" | "cannot_tell"
    visual_match: str  # "strong" | "partial" | "weak"
    reason: str = ""


def candidate_verdict(j: Judgment, exact: list[str]) -> Verdict:
    """Verdict for one candidate recall.

    CONFIRMED  a model number or UPC read from the photo matches this recall, and the verifier agrees
               it is the same kind of product.
    LIKELY     same product type, strong visual match, and nothing visible contradicts brand or model.
    UNSURE     some evidence, but weak or conflicting. Never presented as a match.
    NO MATCH   different product, or a legible model number that is not one of the recalled models.
    """
    has_code = any(e.startswith(("model:", "upc:")) for e in exact)
    if not j.same_product_type:
        return Verdict.NO_MATCH
    if has_code:
        return Verdict.CONFIRMED if j.same_brand != "no" else Verdict.UNSURE
    if j.model_consistent == "no":
        return Verdict.NO_MATCH
    if j.visual_match == "strong" and j.same_brand != "no":
        return Verdict.LIKELY
    if j.visual_match == "weak" or j.same_brand == "no":
        return Verdict.NO_MATCH
    return Verdict.UNSURE


def overall(verdicts: list[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: v.severity, default=Verdict.NO_MATCH)
