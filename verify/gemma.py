"""Gemma 4 vision verifier: the seller's photo next to every candidate recall photo, forced choice."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from PIL import Image

from index.models import load_image
from read.gemma import ItemRead, OllamaVision


def recall_context(conn: sqlite3.Connection, recall_id: int) -> dict[str, Any]:
    title, url, hazards, remedies, mfrs = conn.execute(
        "SELECT title, url, hazards, remedies, manufacturers FROM recalls WHERE recall_id=?", (recall_id,)
    ).fetchone()
    prods = conn.execute("SELECT name, model FROM products WHERE recall_id=?", (recall_id,)).fetchall()
    models = [m for (m,) in conn.execute("SELECT model FROM model_numbers WHERE recall_id=? LIMIT 40", (recall_id,))]
    images = [p for (p,) in conn.execute(
        "SELECT path FROM images WHERE recall_id=? AND path IS NOT NULL ORDER BY path", (recall_id,))]
    return {
        "recall_id": recall_id, "title": title, "url": url,
        "hazards": json.loads(hazards or "[]"), "remedies": json.loads(remedies or "[]"),
        "manufacturers": json.loads(mfrs or "[]"),
        "products": "; ".join(n for n, _ in prods)[:400] or "n/a",
        "models": ", ".join(models)[:400] or "not listed",
        "images": images,
    }


CHOOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "match": {"type": "string", "enum": ["none", "1", "2", "3", "4", "5"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "same_brand": {"type": "string", "enum": ["yes", "no", "cannot_tell"]},
        "model_consistent": {"type": "string", "enum": ["yes", "no", "cannot_tell"]},
        "reason": {"type": "string"},
    },
    "required": ["match", "confidence", "same_brand", "model_consistent", "reason"],
}

CHOOSE_PROMPT = """You are checking whether a secondhand item is a recalled product.
Image 1 is the seller's photo of their item. The next images are official recall photos:
{listing}
What was read from the seller's photo: {read}

Which recall photo shows the SAME product design as the seller's item (same shape, construction and
distinctive details; recalls usually cover several colours and patterns, so ignore colour and print)?
Several may be the same kind of product; pick one only if it is the same design, otherwise "none".
- match: the number of the matching recall photo (2 = first recall photo), or "none".
- confidence: "high" only if distinctive details clearly line up; "medium" if likely; "low" if unsure.
- same_brand / model_consistent: for the chosen recall, as visible on the seller's item ("cannot_tell" if not visible).
- reason: one short sentence.
Respond with JSON only."""


def choose(
    vision: OllamaVision, photo: Image.Image, candidates: list[tuple[str | Image.Image, dict[str, Any]]],
    item: ItemRead | None,
) -> dict[str, Any]:
    """Forced choice among the candidates. Returns the parsed answer with 'index' (0-based) or None."""
    listing = "\n".join(
        f"- Image {i + 2}: {ctx['title']} (models: {ctx['models'][:120]})" for i, (_, ctx) in enumerate(candidates)
    )
    prompt = CHOOSE_PROMPT.format(listing=listing, read=json.dumps(item.to_dict() if item else {}))
    d = vision.chat_json(prompt, [photo] + [load_image(img) for img, _ in candidates], CHOOSE_SCHEMA)
    m = d.get("match", "none")
    idx = int(m) - 2 if m.isdigit() and 2 <= int(m) <= len(candidates) + 1 else None
    if m.isdigit() and int(m) == 1:
        idx = None  # pointed at the seller's own photo
    return {**d, "index": idx}
