"""Short recall cards from CPSC data: brand + product heading, one-word hazard, affected models, what to do.

CPSC titles are long sentences ("Graco Recalls Inclined Sleeper Accessory Included with Four Models of Playards to
Prevent Risk of Suffocation"). Cards show a short heading derived from them, falling back to a shortened title.
"""

from __future__ import annotations

import re

_TAIL = (r"(?:,? Due to\b|,? Because\b|,? to Prevent\b|,? After\b|,? Following\b|;|,? Sold\b|,? Imported\b"
         r"|,? Included\b|,? Can\b|,? Could\b|,? May\b|$)")
_NOT_BRAND = re.compile(r"\b(Can|Could|May|Due|Risk|Hazard)\b")
_RECALLS = re.compile(rf"^(?P<brand>.+?) (?:Expands Recall of|Recalls|Recall)(?: of)? (?P<product>.+?){_TAIL}")
_RECALLED_BY = re.compile(rf"^(?P<product>.+?) Recalled by (?P<brand>.+?){_TAIL}")
_RECALLED = re.compile(rf"^(?P<product>.+?) (?:Are |Is )?Recalled{_TAIL}")
_MAKER = re.compile(r"(?:Made by|Manufactured by|Imported by|Distributed by|Sold (?:Exclusively )?(?:on|at|by) "
                    r"[^;]*? by) (?P<brand>[^;(),]+)", re.I)

# Hazard words, matched as word stems. The tag is the hazard the recall names first ("Due to Tip-Over and
# Entrapment Hazards" is Tip-over), so CPSC's own ordering decides the primary hazard. Generic words only count
# when nothing specific is named.
_HAZARDS = [
    (r"tip[- ]?over", "Tip-over"), (r"suffocat", "Suffocation"), (r"strangulat", "Strangulation"),
    (r"entrap", "Entrapment"), (r"chok", "Choking"), (r"aspirat", "Choking"), (r"drown", "Drowning"),
    (r"carbon monoxide", "Poisoning"), (r"poison", "Poisoning"), (r"child[- ]resistant", "Poisoning"),
    (r"ingest", "Ingestion"), (r"magnet", "Ingestion"), (r"\blead\b", "Lead"), (r"electrocut", "Shock"),
    (r"\bshock", "Shock"), (r"flammab", "Fire"), (r"\bfire", "Fire"), (r"\bburn", "Burns"), (r"scald", "Burns"),
    (r"lacerat", "Cuts"), (r"amputat", "Amputation"), (r"\bfall", "Falls"), (r"crash", "Crash"),
    (r"pinch", "Pinching"), (r"\bmold\b", "Mold"), (r"bacteri", "Infection"), (r"explos", "Explosion"),
]
_GENERIC = [(r"impact", "Impact"), (r"injur", "Injury")]


def _first_hazard(text: str, table: list[tuple[str, str]]) -> str | None:
    hits = [(m.start(), tag) for rx, tag in table for m in [re.search(rx, text)] if m]
    return min(hits)[1] if hits else None


def _clean(s: str) -> str:
    s = re.sub(r"\s*\((?:Recall Alert|Updated)\)\s*", " ", s)
    return re.sub(r"\s+", " ", s).strip(" ,;.")


def _join(brand: str, product: str) -> str:
    product = re.sub(r"^to (?:Repair|Replace|Refund|Remedy) ", "", product)
    """Brand + product in CPSC's own capitalization (product names are often proper nouns), without repeating the
    brand when the product already starts with it ("Amazon" + "Amazon Basics Dumbbells")."""
    if product.lower().startswith(brand.lower()):
        return product
    return f"{brand} {product}"


def heading(title: str, manufacturers: list[str] | None = None) -> str:
    t = _clean(title or "")
    m = _RECALLS.match(t) or _RECALLED_BY.match(t)
    if m:
        brand, product = _clean(m.group("brand")), _clean(m.group("product"))
        if len(brand) <= 40 and product and not _NOT_BRAND.search(brand):
            return _trim(_join(brand, product))
    m = _RECALLED.match(t)
    if m and m.group("product"):
        product = _clean(m.group("product"))
        maker = _MAKER.search(t)
        brand = _clean(maker.group("brand")) if maker else (manufacturers or [""])[0].split(",")[0].strip()
        if brand and len(brand) <= 40 and brand.lower() not in product.lower():
            return _trim(_join(brand, product))
        return _trim(product)
    return _trim(re.split(r",? Due to |;", t)[0])


def _trim(s: str, n: int = 72) -> str:
    return s if len(s) <= n else s[: s.rfind(" ", 0, n - 1)].rstrip(" ,;") + "…"


def hazard_tag(title: str, hazards: list[str]) -> str:
    t = (title or "").lower()
    focus = t.split(" due to ", 1)[1] if " due to " in t else ""
    for text in (focus, " ".join(hazards).lower(), t):
        tag = _first_hazard(text, _HAZARDS)
        if tag:
            return tag
    for text in (focus, " ".join(hazards).lower(), t):
        tag = _first_hazard(text, _GENERIC)
        if tag:
            return tag
    return "Injury"


def what_to_do(remedies: list[str]) -> str:
    s = (remedies or [""])[0].strip()
    if not s:
        return ""
    s = re.sub(r"^(?:Consumers|Owners|Parents|Caregivers)(?: and [a-z]+)? should (?:immediately )?", "", s)
    s = s[:1].upper() + s[1:]
    first = re.split(r"(?<=[.!?])\s", s, maxsplit=1)[0]
    return _trim(first, 180)


def card_fields(row: dict, models: list[str]) -> dict:
    return {
        "heading": heading(row.get("title", ""), row.get("manufacturers")),
        "hazard_tag": hazard_tag(row.get("title", ""), row.get("hazards") or []),
        "models": models[:6],
        "models_more": max(0, len(models) - 6),
        "what_to_do": what_to_do(row.get("remedies") or []),
    }
