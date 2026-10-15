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
    image_sim: float | None = None, if_text_led: bool | None = None, brand_conflict: list[bool] | None = None,
) -> tuple[Verdict, int | None]:
    """Return (verdict, index of the shortlist candidate it refers to).

    CONFIRMED       a model number or UPC read off the item matches a shortlisted recall, and no brand read off the
                    item contradicts that recall (brand_conflict; added after Run 3, chosen on the dev split).
    POSSIBLE MATCH  no code match, but the closest recall photo is similar enough (or the photo is a label
                    whose words led the search): show the shortlist and ask for a label photo.
    NO MATCH FOUND  otherwise. Means no match in CPSC recalls, never "safe".
    """
    image_sim = POSSIBLE_IMAGE_SIM if image_sim is None else image_sim
    if_text_led = POSSIBLE_IF_TEXT_LED if if_text_led is None else if_text_led
    conflict = brand_conflict or [False] * len(exact)
    coded = [i for i, e in enumerate(exact) if has_code(e) and not conflict[i]]
    if coded:
        return Verdict.CONFIRMED, coded[0]
    scores = [s if s is not None else -1.0 for s in image_scores]
    if scores and max(scores) >= image_sim:
        return Verdict.POSSIBLE, None
    if text_led and if_text_led and exact:
        return Verdict.POSSIBLE, None
    return Verdict.NO_MATCH, None


# Display policy (not a verdict): when nothing triggers, children's products still get their three closest recalls,
# shown in grey as suggestions to check, never as a match. Chosen on the dev split (eval/display_policy.py): it raised
# "right recall shown" for children's products from 25% to 59% there.
CLOSEST_FOR_CHILDREN = True


def show_closest(verdict: Verdict, child_product: bool, top_category: str | None) -> bool:
    from ingest.categories import CHILD_CATEGORIES

    if verdict != Verdict.NO_MATCH or not CLOSEST_FOR_CHILDREN:
        return False
    return child_product or top_category in CHILD_CATEGORIES
