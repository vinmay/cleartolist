"""Turn retrieval evidence into one of three verdicts. Pure logic, unit-tested.

Only a code match is an alarm. A shortlist that merely looks similar is shown as POSSIBLE MATCH, which asks
the user to photograph the label and never claims the item is recalled.
"""

from __future__ import annotations

from enum import Enum


class Verdict(str, Enum):
    CONFIRMED = "CONFIRMED"
    POSSIBLE = "POSSIBLE MATCH"
    NO_MATCH = "NO MATCH FOUND"

    @property
    def severity(self) -> int:
        return {"CONFIRMED": 2, "POSSIBLE MATCH": 1, "NO MATCH FOUND": 0}[self.value]


# When the shortlist is worth showing. Chosen on the dev split only (eval/gate.py); see eval/results.md.
POSSIBLE_IMAGE_SIM = 0.81  # closest recall photo in the shortlist, SigLIP 2 cosine
POSSIBLE_IF_TEXT_LED = False  # label photos whose words led the search; on dev this cost more than it found


def has_code(exact: list[str]) -> bool:
    return any(e.startswith(("model:", "upc:")) for e in exact)


def decide(
    exact: list[list[str]], image_scores: list[float | None], text_led: bool = False,
    image_sim: float | None = None, if_text_led: bool | None = None,
) -> tuple[Verdict, int | None]:
    """Return (verdict, index of the shortlist candidate it refers to).

    CONFIRMED       a model number or UPC read off the item matches a shortlisted recall.
    POSSIBLE MATCH  no code match, but the closest recall photo is similar enough (or the photo is a label
                    whose words led the search): show the shortlist and ask for a label photo.
    NO MATCH FOUND  otherwise. Means no match in CPSC recalls, never "safe".
    """
    image_sim = POSSIBLE_IMAGE_SIM if image_sim is None else image_sim
    if_text_led = POSSIBLE_IF_TEXT_LED if if_text_led is None else if_text_led
    coded = [i for i, e in enumerate(exact) if has_code(e)]
    if coded:
        return Verdict.CONFIRMED, coded[0]
    scores = [s if s is not None else -1.0 for s in image_scores]
    if scores and max(scores) >= image_sim:
        return Verdict.POSSIBLE, None
    if text_led and if_text_led and exact:
        return Verdict.POSSIBLE, None
    return Verdict.NO_MATCH, None
