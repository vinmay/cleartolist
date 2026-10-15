"""Keyword categorizer for CPSC recalls.

Used to (1) drop out-of-scope recalls (motor vehicles) from the index, (2) flag car seats,
which NHTSA regulates rather than CPSC, and (3) split the eval into a children's-products
run and a mixed-category run with per-category breakdowns.
"""

from __future__ import annotations

import re

# Ordered: the first matching rule wins. Children's subcategories come first so that
# "crib mattress" lands in cribs, not furniture.
CHILD_RULES: list[tuple[str, str]] = [
    ("car_seat", r"\bcar seats?\b|\bbooster seats?\b|\binfant seats?\b"),
    ("crib_bassinet_sleeper", r"\bcribs?\b|\bbassinets?\b|\bsleepers?\b|\bsleep(ing)? sacks?\b|\bcradles?\b|"
                              r"\bbedside sleeper|\binclined sleep|\bnursery\b|\bmoses baskets?\b|\bloungers?\b"),
    ("stroller", r"\bstrollers?\b|\bjogg(er|ing) stroller|\bpram\b|\bbuggy\b"),
    ("high_chair", r"\bhigh ?chairs?\b|\bbooster chairs?\b|\bhook-on chairs?\b"),
    ("play_yard", r"\bplay ?yards?\b|\bplaypens?\b|\bplay pens?\b|\bplayards?\b"),
    ("toy", r"\btoys?\b|\bdolls?\b|\bplush\b|\bstuffed\b|\brattles?\b|\bteethers?\b|\bpuzzles?\b|\bslime\b|"
            r"\bbuilding (set|blocks?)\b|\bplay ?sets?\b|\bfidget\b|\bkids?'? (game|kit)\b"),
    ("other_child", r"\binfants?\b|\bbaby\b|\bbabies\b|\btoddlers?\b|\bchildren'?s\b|\bkids?\b|\byouth\b|"
                    r"\bpacifiers?\b|\bbouncers?\b|\bbaby swings?\b|\bwalkers?\b|\bbaby carriers?\b|"
                    r"\bchanging (table|pad)s?\b|\bbath seats?\b|\bbibs?\b|\bnursing\b|\bteething\b"),
]

OTHER_RULES: list[tuple[str, str]] = [
    ("vehicle", r"\batvs?\b|\butvs?\b|\brovs?\b|\boff-?road vehicles?\b|\brecreational off-highway\b|"
                r"\boff-highway\b|\ball-terrain vehicles?\b|\bside-by-sides?\b|\bmotorcycles?\b|\bdirt bikes?\b|"
                r"\bsnowmobiles?\b|\bgo-?karts?\b|\bgolf carts?\b|\butility vehicles?\b|\bminibikes?\b|"
                r"\btransportation vehicles?\b|\bpersonal watercraft\b"),
    ("furniture", r"\bdressers?\b|\bchests? of drawers\b|\bwardrobes?\b|\bbookcases?\b|\bcabinets?\b|"
                  r"\bchairs?\b|\btables?\b|\bdesks?\b|\bsofas?\b|\brecliners?\b|\bbunk beds?\b|\bbed frames?\b|"
                  r"\bmattress(es)?\b|\bshelv(es|ing)\b|\bnightstands?\b|\bstools?\b|\bottomans?\b"),
    ("heating_cooling", r"\bheaters?\b|\bfans?\b|\bair conditioners?\b|\bdehumidifiers?\b|\bhumidifiers?\b|"
                        r"\bfireplaces?\b|\bstoves?\b|\bfurnaces?\b|\bwater heaters?\b"),
    ("kitchen", r"\bblenders?\b|\bcookers?\b|\bpressure cook|\bair fryers?\b|\bkettles?\b|\btoasters?\b|"
                r"\bcoffee (maker|machine)s?\b|\bmicrowaves?\b|\bovens?\b|\bmixers?\b|\bknives\b|\bknife\b|"
                r"\bcookware\b|\bpans?\b|\bpots?\b|\bmugs?\b|\bcups?\b|\bbottles?\b|\btumblers?\b|\bdishwashers?\b|"
                r"\brefrigerators?\b|\bfreezers?\b|\bgriddles?\b|\bgrills?\b|\bjuicers?\b|\bfood processors?\b"),
    ("electrical_battery", r"\bchargers?\b|\bpower banks?\b|\bbatter(y|ies)\b|\bextension cords?\b|"
                           r"\bpower strips?\b|\bsurge protectors?\b|\badapters?\b|\boutlets?\b|\blamps?\b|"
                           r"\blights?\b|\blighting\b|\bsmoke alarms?\b|\bdetectors?\b|\bheadphones?\b|"
                           r"\bspeakers?\b|\blaptops?\b|\bcomputers?\b|\bphones?\b|\bcables?\b"),
    ("tools_outdoor", r"\bsaws?\b|\bdrills?\b|\bmowers?\b|\btrimmers?\b|\bchainsaws?\b|\bblowers?\b|"
                      r"\bgenerators?\b|\bpressure washers?\b|\bladders?\b|\btools?\b|\bsnow ?throwers?\b|"
                      r"\btillers?\b|\bpatio\b|\bumbrellas?\b|\bfire pits?\b|\btorches\b"),
    ("sports_rec", r"\bbicycles?\b|\bbikes?\b|\be-?bikes?\b|\bscooters?\b|\bhelmets?\b|\bskateboards?\b|"
                   r"\bhoverboards?\b|\btreadmills?\b|\bexercise\b|\bfitness\b|\bweights?\b|\bkayaks?\b|"
                   r"\bpaddle ?boards?\b|\btrampolines?\b|\bclimbing\b|\bcamping\b|\btents?\b|\bfishing\b|"
                   r"\bgolf\b|\bballs?\b|\bswimming\b|\bpools?\b|\bharness(es)?\b|"
                   r"\bcarabiners?\b|\bski\b|\bbindings?\b|\bscuba\b|\bpaintball\b|\btricycles?\b|\bice axes?\b"),
    ("apparel", r"\bjackets?\b|\bhoodies?\b|\bsweatshirts?\b|\bpajamas?\b|\bshirts?\b|\bpants\b|\bdresses\b|"
                r"\bshoes?\b|\bboots?\b|\bsandals?\b|\bclothing\b|\bgarments?\b|\bdrawstrings?\b|\brobes?\b"),
    ("packaging_chemicals", r"\bchild[- ]resistant packaging\b|\bessential oils?\b|\bwintergreen\b|\blidocaine\b|"
                            r"\bprescription\b|\bsupplements?\b|\bwipes\b|\blighters?\b|\bfireworks?\b|\bsparklers?\b"),
    ("appliance_fixture", r"\branges?\b|\bcooktops?\b|\bboilers?\b|\bfaucets?\b|\bwater filters?\b|\btubs?\b|"
                          r"\btoilets?\b|\bgarage door\b|\bwater dispensers?\b|\bice makers?\b|\bsmokers?\b"),
    ("home", r"\bcandles?\b|\bblankets?\b|\bpillows?\b|\bcurtains?\b|\bblinds\b|\brugs?\b|\bmirrors?\b|"
             r"\bvacuums?\b|\bwashers?\b|\bdryers?\b|\birons?\b|\bdiffusers?\b|\bdecor\b|\bgates?\b|"
             r"\bcleaners?\b|\bdetergent\b|\bcontainers?\b|\bjars?\b|\bcosmetics?\b|\bhair dryers?\b"),
]

CHILD_CATEGORIES = {c for c, _ in CHILD_RULES} - {"car_seat"}
OUT_OF_SCOPE = {"vehicle"}

_CHILD = [(c, re.compile(p, re.I)) for c, p in CHILD_RULES]
_OTHER = [(c, re.compile(p, re.I)) for c, p in OTHER_RULES]


def categorize(title: str, product_names: list[str] | None = None, product_types: list[str] | None = None) -> str:
    """Return a category slug. The title is the strongest signal (CPSC titles name the product)."""
    # Drop the hazard clause: "Faucets Recalled Due to Lead Exposure for Young Children" is not a children's product.
    title = re.split(r"\b(?:Due to|Because of)\b|;", title or "", maxsplit=1, flags=re.I)[0]
    texts = [title, " ".join(product_names or []), " ".join(product_types or [])]
    # Vehicles first: "Youth ATVs" are vehicles, not children's products.
    for text in texts:
        if _OTHER[0][1].search(text):
            return "vehicle"
    for text in texts:
        for cat, rx in _CHILD:
            if rx.search(text):
                return cat
        for cat, rx in _OTHER[1:]:
            if rx.search(text):
                return cat
    return "other"


def is_child(category: str) -> bool:
    return category in CHILD_CATEGORIES
