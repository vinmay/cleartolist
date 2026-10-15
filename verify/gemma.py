"""Gemma 4 vision verifier: compare the user's photo with a candidate recall's photo and text."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from PIL import Image

from index.models import load_image
from read.gemma import ItemRead, OllamaVision
from verify.verdict import Judgment

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_product_type": {"type": "boolean"},
        "same_brand": {"type": "string", "enum": ["yes", "no", "cannot_tell"]},
        "model_consistent": {"type": "string", "enum": ["yes", "no", "cannot_tell"]},
        "visual_match": {"type": "string", "enum": ["strong", "partial", "weak"]},
        "reason": {"type": "string"},
    },
    "required": ["same_product_type", "same_brand", "model_consistent", "visual_match", "reason"],
}

VERIFY_PROMPT = """You are checking whether a secondhand item matches a product recall.
Image 1 is the seller's photo of their item. Image 2 is the official recall photo.

Recall: {title}
Recalled products: {products}
Recalled model numbers: {models}
What was read from the seller's photo: {read}

Answer strictly:
- same_product_type: is the seller's item the same kind of product as the recalled one (e.g. both strollers)?
- same_brand: does a brand visible on the seller's item match the recalling brand? "cannot_tell" if no brand is visible.
- model_consistent: if a model number is legible on the seller's item, is it one of the recalled model numbers?
  "cannot_tell" if no model number is legible. Never guess.
- visual_match: "strong" only if shape, design details and colourway look like the same product line;
  "partial" if similar but not clearly the same design; "weak" if they look different.
- reason: one short sentence.
Respond with JSON only."""


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


def judge(
    vision: OllamaVision, photo: Image.Image, recall_image: str | Image.Image, ctx: dict[str, Any],
    item: ItemRead | None,
) -> Judgment:
    prompt = VERIFY_PROMPT.format(
        title=ctx["title"], products=ctx["products"], models=ctx["models"],
        read=json.dumps(item.to_dict() if item else {}),
    )
    d = vision.chat_json(prompt, [photo, load_image(recall_image)], VERIFY_SCHEMA)
    return Judgment(
        same_product_type=bool(d.get("same_product_type")),
        same_brand=d.get("same_brand", "cannot_tell"),
        model_consistent=d.get("model_consistent", "cannot_tell"),
        visual_match=d.get("visual_match", "weak"),
        reason=d.get("reason", ""),
    )
