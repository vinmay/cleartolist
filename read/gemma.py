"""Gemma 4 vision via a local Ollama: read a product photo into strict JSON."""

from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from PIL import Image

from index.models import OLLAMA_HOST, load_image

VISION_MODEL = os.environ.get("CTL_VISION_MODEL", "gemma4:e4b")

READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": "string",
                     "description": "specific item type a shopper would search for, e.g. 'high chair', 'baby lounger', "
                                    "'sleep sack', 'jogging stroller', 'space heater'; not a department like 'toys'"},
        "brand": {"type": "string", "description": "brand or manufacturer name if visible or recognizable, else ''"},
        "visible_text": {"type": "string", "description": "all legible text on the item or label"},
        "model_number": {"type": "string", "description": "model / item / style number if legible, else ''"},
        "upc": {"type": "string", "description": "UPC / barcode digits if legible, else ''"},
        "description": {"type": "string", "description": "one sentence: shape, colours, materials, features"},
        "child_product": {"type": "boolean", "description": "is this a product for babies or children"},
    },
    "required": ["category", "brand", "visible_text", "model_number", "upc", "description", "child_product"],
}

READ_PROMPT = """You are helping a secondhand reseller identify an item before listing it.
Look at the photo and describe the product. For "category" name the specific kind of item (for example
"high chair", "bassinet", "baby lounger", "sleep sack", "stroller", "toy car", "dresser", "space heater"),
never a broad department such as "toys", "electronics" or "baby products".
Only report brand, model number and UPC if you can actually
read them or the brand is unmistakable; never invent them. If not visible, leave the field as "" (nothing).
Respond with JSON only."""


@dataclass
class ItemRead:
    category: str = ""
    brand: str = ""
    visible_text: str = ""
    model_number: str = ""
    upc: str = ""
    description: str = ""
    child_product: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def query_text(self) -> str:
        """Text used to search the recall index."""
        parts = [self.brand, self.category, self.description, self.model_number]
        return " ".join(p for p in parts if p).strip()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw")
        return d


def encode_image(img: Image.Image, max_side: int = 896) -> str:
    img = img.copy()
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


class OllamaVision:
    """Thin client for chat calls with images and a JSON schema."""

    def __init__(self, model: str = VISION_MODEL, host: str = OLLAMA_HOST, timeout: float = 600):
        self.model = model
        self.client = httpx.Client(base_url=host, timeout=timeout)

    def chat_json(self, prompt: str, images: list[Image.Image], schema: dict[str, Any],
                  system: str | None = None) -> dict[str, Any]:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt, "images": [encode_image(i) for i in images]})
        # Small models occasionally loop inside a string field until the token cap cuts the JSON off;
        # retry with a little temperature to break the loop.
        last: Exception | None = None
        for temperature in (0.0, 0.3, 0.6):
            r = self.client.post("/api/chat", json={
                "model": self.model,
                "messages": msgs,
                "format": schema,
                "stream": False,
                "think": False,
                "keep_alive": "15m",
                "options": {"temperature": temperature, "num_ctx": 4096, "num_predict": 600,
                            "repeat_penalty": 1.15},
            })
            r.raise_for_status()
            try:
                return json.loads(r.json()["message"]["content"])
            except json.JSONDecodeError as e:
                last = e
        raise ValueError(f"{self.model} returned invalid JSON three times") from last


_EMPTY = {"", "empty string", "empty", "none", "unknown", "n/a", "na", "not visible", "not legible", "null", "''"}


def clean_field(v: object) -> str:
    """Small models sometimes write the placeholder instead of leaving the field empty."""
    s = str(v or "").strip().strip('"').strip()
    return "" if s.lower() in _EMPTY else s


def read_item(img: Image.Image | str, client: OllamaVision | None = None) -> ItemRead:
    client = client or OllamaVision()
    img = load_image(img)
    data = client.chat_json(READ_PROMPT, [img], READ_SCHEMA)
    known = {k: clean_field(data.get(k)) for k in READ_SCHEMA["properties"] if k != "child_product"}
    return ItemRead(**known, child_product=bool(data.get("child_product")), raw=data)


if __name__ == "__main__":
    import sys
    import time

    c = OllamaVision()
    for p in sys.argv[1:]:
        t = time.time()
        print(p, json.dumps(read_item(p, c).to_dict()), f"{time.time() - t:.1f}s")
