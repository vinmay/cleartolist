"""Fetch licensed negative photos: same-category products that are not recall photos.

Source: Wikimedia Commons (CC0 / public domain / CC BY / CC BY-SA only). Each kept file
is recorded in eval/negatives_manifest.json with its source page, author and license, so
the set is reproducible and attributable. The images themselves go to data/negatives/.

Candidates are filtered automatically:
  1. SigLIP zero-shot: the photo must look like its target category more than any other.
  2. Gemma 4 check: the product is the clear subject, photographed like a listing photo
     (no paintings, historical black-and-white photos or people-centred shots).
  3. Not a near-duplicate (cosine >= 0.92) of any CPSC recall image.
We cannot prove that a Commons photo does not show a recalled model; the manifest notes
this, and the false-alarm numbers should be read with it in mind.

    uv run python -m eval.negatives --per-category 12
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import torch
from PIL import Image

from index.build import VectorStore, image_store_path
from index.models import image_embedder, load_image
from read.gemma import OllamaVision

NEG_DIR = Path("data/negatives")
MANIFEST = Path("eval/negatives_manifest.json")
API = "https://commons.wikimedia.org/w/api.php"
UA = "ClearToList-eval/0.1 (https://github.com/vinmay/cleartolist; research eval set)"
OK_LICENSES = re.compile(r"^(CC0|Public domain|PD|CC BY(-SA)? [1-4]\.0)", re.I)

# eval category -> (Commons search terms, zero-shot label)
QUERIES: dict[str, tuple[list[str], str]] = {
    # children's run
    "crib_bassinet_sleeper": (["baby crib", "bassinet", "cot bed baby", "baby cot", "crib nursery"],
                              "a baby crib or bassinet"),
    "stroller": (["baby stroller", "pushchair", "pram baby", "buggy stroller", "jogging stroller"], "a baby stroller"),
    "high_chair": (["high chair baby", "highchair", "children's high chair", "baby chair feeding"],
                   "a baby high chair"),
    "play_yard": (["playpen baby", "play yard baby", "travel cot", "baby playpen"], "a baby playpen"),
    "toy": (["children's toy", "wooden toy", "plush toy", "toy car"], "a children's toy"),
    "other_child": (["baby bouncer", "baby carrier", "baby walker", "baby bath seat"], "a baby product"),
    # mixed run
    "furniture": (["dresser furniture", "chest of drawers", "bookcase"], "a piece of furniture"),
    "heating_cooling": (["space heater", "electric fan", "portable heater"], "a heater or fan"),
    "kitchen": (["electric kettle", "blender kitchen", "toaster", "pressure cooker"], "a kitchen appliance"),
    "electrical_battery": (["power bank", "phone charger", "table lamp"], "a charger, battery or lamp"),
    "tools_outdoor": (["cordless drill", "lawn mower", "chainsaw"], "a power tool or garden tool"),
    "sports_rec": (["bicycle helmet", "bicycle", "electric scooter"], "a bicycle, helmet or scooter"),
    "home": (["candle jar", "vacuum cleaner", "hair dryer"], "a household item"),
}
DISTRACTOR_LABELS = ["a person", "a building", "a landscape", "a drawing or diagram", "a car", "an animal",
                     "text or a document", "a group of people", "a painting", "an old black and white photograph",
                     "a parent holding a baby"]

GEMMA_CHECK = {
    "type": "object",
    "properties": {
        "main_subject": {"type": "string", "enum": ["a product or object", "people", "a drawing or painting",
                                                     "an old black-and-white photo", "a building or scene"]},
        "is_target": {"type": "boolean"},
    },
    "required": ["main_subject", "is_target"],
}
GEMMA_PROMPT = "What is the main subject of this photo? Then: is the main object {label}? JSON only."


def gemma_accepts(answer: dict) -> bool:
    return answer.get("main_subject") == "a product or object" and bool(answer.get("is_target"))


def commons_search(client: httpx.Client, term: str, limit: int = 50) -> list[dict]:
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{term} filetype:bitmap", "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 800,
    }
    for attempt in range(4):
        r = client.get(API, params=params)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(1)
        return list(r.json().get("query", {}).get("pages", {}).values())
    return []


def clean(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-category", type=int, default=12)
    a = ap.parse_args()
    emb = image_embedder()
    vision = OllamaVision()
    store = VectorStore.load(image_store_path())
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(emb.name)
    labels = [lab for _, lab in QUERIES.values()] + DISTRACTOR_LABELS
    with torch.inference_mode():
        t = tok([f"This is a photo of {lab}." for lab in labels], padding="max_length", max_length=64,
                return_tensors="pt").to(emb.dev)
        tf = emb.model.get_text_features(**t)
        if not isinstance(tf, torch.Tensor):
            tf = tf.pooler_output
        text_vecs = torch.nn.functional.normalize(tf.float(), dim=-1).cpu().numpy()

    NEG_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    seen: set[str] = set()
    client = httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
    for li, (cat, (terms, _)) in enumerate(QUERIES.items()):
        kept = 0
        for term in terms:
            if kept >= a.per_category:
                break
            for page in commons_search(client, term):
                if kept >= a.per_category:
                    break
                info = (page.get("imageinfo") or [{}])[0]
                meta = info.get("extmetadata", {})
                lic = clean(meta.get("LicenseShortName", {}).get("value", ""))
                if page["title"] in seen or info.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                if not OK_LICENSES.match(lic):
                    continue
                seen.add(page["title"])
                try:
                    r = client.get(info["thumburl"])
                    r.raise_for_status()
                    img = load_image(Image.open(io.BytesIO(r.content)))
                except Exception:
                    continue
                time.sleep(0.5)
                v = emb.embed([img])[0]
                sims = text_vecs @ v
                if int(np.argmax(sims)) != li:
                    continue
                if float((store.vectors @ v).max()) >= 0.92:
                    continue  # looks like an actual recall photo
                try:
                    ok = vision.chat_json(GEMMA_PROMPT.format(label=QUERIES[cat][1]), [img], GEMMA_CHECK)
                except Exception:
                    continue
                if not gemma_accepts(ok):
                    continue
                out = NEG_DIR / f"{cat}__{len(manifest):03d}.jpg"
                img.save(out, quality=92)
                manifest.append({
                    "file": out.name, "category": cat, "search_term": term,
                    "source_page": info.get("descriptionurl"), "image_url": info.get("thumburl"),
                    "title": page["title"], "author": clean(meta.get("Artist", {}).get("value", ""))[:200],
                    "license": lic, "license_url": meta.get("LicenseUrl", {}).get("value"),
                })
                kept += 1
        print(f"{cat}: kept {kept}", file=sys.stderr)
    MANIFEST.write_text(json.dumps({
        "source": "Wikimedia Commons",
        "note": "Same-category product photos used as negatives. Filtered by SigLIP zero-shot category match, "
                "a Gemma 4 listing-photo check, and by not being a near-duplicate (cosine >= 0.92) of any "
                "CPSC recall image. Not individually "
                "verified as non-recalled models.",
        "items": manifest,
    }, indent=1))
    print(f"{len(manifest)} negatives -> {MANIFEST}")


if __name__ == "__main__":
    main()
