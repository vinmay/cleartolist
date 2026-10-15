"""Map the verifier's forced choice + exact-match evidence to a verdict. Pure logic, unit-tested."""

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
class Choice:
    """What the verifier answered when shown the seller's photo next to all candidate recall photos."""

    index: int | None  # 0-based candidate the verifier says is the same product design, or None
    confidence: str = "low"  # "high" | "medium" | "low"
    same_brand: str = "cannot_tell"  # "yes" | "no" | "cannot_tell"
    model_consistent: str = "cannot_tell"  # "yes" | "no" | "cannot_tell"
    reason: str = ""


# If the verifier picks nothing but the closest recall photo is this similar (SigLIP 2 cosine), say UNSURE
# rather than NO MATCH: missing a recall is the worse error for a safety screen. Tuned on the dev split.
UNSURE_IMAGE_SIM = 0.80


def has_code(exact: list[str]) -> bool:
    return any(e.startswith(("model:", "upc:")) for e in exact)


def decide(choice: Choice, exact: list[list[str]], top_image_score: float | None = None) -> tuple[Verdict, int | None]:
    """Return (verdict, index of the candidate it refers to).

    CONFIRMED  a model number / UPC read from the photo matches a recall, and the verifier picked that recall.
    LIKELY     the verifier picked a recall with high confidence; no visible brand or model contradicts it.
    UNSURE     conflicting or weaker evidence: a code hit the verifier did not pick, a pick with lower
               confidence, a pick whose visible brand differs, or no pick while the closest recall photo is
               very similar (top_image_score >= UNSURE_IMAGE_SIM). Never presented as a match.
    NO MATCH   no pick and no code hit, or the visible model number is not one of the recalled models.
    """
    coded = [i for i, e in enumerate(exact) if has_code(e)]
    i = choice.index
    if i is not None and i in coded:
        return Verdict.CONFIRMED, i
    if coded and i is None:
        return Verdict.UNSURE, coded[0]  # the label says recalled, the photo comparison disagrees
    if i is None:
        if top_image_score is not None and top_image_score >= UNSURE_IMAGE_SIM:
            return Verdict.UNSURE, 0
        return Verdict.NO_MATCH, None
    if choice.model_consistent == "no":
        return Verdict.NO_MATCH, None
    if coded:
        return Verdict.UNSURE, coded[0]  # code points at one recall, the photo at another
    if choice.confidence == "high" and choice.same_brand != "no":
        return Verdict.LIKELY, i
    return Verdict.UNSURE, i
