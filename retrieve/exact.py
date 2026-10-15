"""Exact lookups against the recall tables: model numbers, UPCs, brand / manufacturer."""

from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache

from ingest.cpsc import normalize_manufacturer, normalize_model, normalize_upc

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-/ ]{2,}[A-Za-z0-9]")
_NOISE = {"NONE", "UNKNOWN", "NA", "N/A", ""}


def model_candidates(model_number: str, visible_text: str) -> set[str]:
    """Normalized model-number tokens from what Gemma read. Must contain a digit and be 4+ chars."""
    out: set[str] = set()
    for raw in [model_number] + re.split(r"[\s,;:|]+", visible_text or ""):
        if not raw or raw.strip().upper() in _NOISE:
            continue
        n = normalize_model(raw)
        if 4 <= len(n) <= 20 and re.search(r"\d", n) and not re.fullmatch(r"(19|20)\d\d", n):
            out.add(n)
    # also try the model field with internal spaces removed ("AB 123" -> "AB123")
    if model_number and model_number.strip().upper() not in _NOISE:
        n = normalize_model(model_number)
        if 4 <= len(n) <= 20:
            out.add(n)
    return out


def upc_candidates(upc: str, visible_text: str) -> set[str]:
    out = set()
    for raw in [upc] + re.findall(r"\d[\d \-]{9,16}\d", visible_text or ""):
        n = normalize_upc(raw or "")
        if 11 <= len(n) <= 13:
            out.add(n)
    return out


def lookup_models(conn: sqlite3.Connection, models: set[str]) -> dict[int, list[str]]:
    hits: dict[int, list[str]] = {}
    for m in models:
        for (rid,) in conn.execute("SELECT DISTINCT recall_id FROM model_numbers WHERE model=?", (m,)):
            hits.setdefault(rid, []).append(f"model:{m}")
    return hits


def lookup_upcs(conn: sqlite3.Connection, upcs: set[str]) -> dict[int, list[str]]:
    hits: dict[int, list[str]] = {}
    for u in upcs:
        for (rid,) in conn.execute("SELECT DISTINCT recall_id FROM upcs WHERE upc=?", (u,)):
            hits.setdefault(rid, []).append(f"upc:{u}")
    return hits


@lru_cache(maxsize=1)
def _brand_table(db_path: str) -> list[tuple[int, str]]:
    conn = sqlite3.connect(db_path)
    rows = []
    for rid, title, mfrs in conn.execute("SELECT recall_id, title, manufacturers FROM recalls"):
        names = " | ".join(normalize_manufacturer(m) for m in json.loads(mfrs or "[]"))
        rows.append((rid, f"{names} | {normalize_manufacturer(title or '')}"))
    return rows


def lookup_brand(db_path: str, brand: str, limit: int = 200) -> list[int]:
    """Recalls whose manufacturer list or title mentions the brand as whole words."""
    b = normalize_manufacturer(brand or "")
    if len(b) < 3 or b.upper() in _NOISE or b in {"unknown", "generic", "none"}:
        return []
    rx = re.compile(rf"\b{re.escape(b)}\b")
    return [rid for rid, text in _brand_table(db_path) if rx.search(text)][:limit]


def brand_conflict(db_path: str, brand: str, recall_id: int) -> bool:
    """A brand was read off the item and the recall's manufacturer list and title don't mention it.

    Used to veto a model-number match: a short code like "100D" or "4000" can appear in unrelated text on a label.
    """
    b = normalize_manufacturer(brand or "")
    if len(b) < 3 or b.upper() in _NOISE or b in {"unknown", "generic", "none"}:
        return False
    return recall_id not in set(lookup_brand(db_path, brand, limit=1_000_000))
