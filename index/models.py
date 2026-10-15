"""Small interfaces around the embedding models so checkpoints can be swapped."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol, Sequence

import numpy as np
import torch
from PIL import Image, ImageOps

DEFAULT_IMAGE_MODEL = os.environ.get("CTL_IMAGE_MODEL", "google/siglip2-base-patch16-224")
DEFAULT_TEXT_MODEL = os.environ.get("CTL_TEXT_MODEL", "embeddinggemma:300m")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")


def device() -> str:
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_image(path_or_img: str | Image.Image) -> Image.Image:
    img = Image.open(path_or_img) if isinstance(path_or_img, str) else path_or_img
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return img.convert("RGB")


class ImageEmbedder(Protocol):
    name: str
    dim: int

    def embed(self, images: Sequence[Image.Image]) -> np.ndarray: ...


class TextEmbedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str], titles: Sequence[str] | None = None) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _l2(x: np.ndarray) -> np.ndarray:
    return x / np.clip(np.linalg.norm(x, axis=-1, keepdims=True), 1e-12, None)


class HFImageEmbedder:
    """SigLIP / SigLIP 2 / CLIP image tower from Hugging Face transformers."""

    def __init__(self, name: str = DEFAULT_IMAGE_MODEL):
        from transformers import AutoModel, AutoProcessor

        self.name = name
        self.dev = device()
        dtype = torch.float16 if self.dev == "cuda" else torch.float32
        self.model = AutoModel.from_pretrained(name, dtype=dtype).to(self.dev).eval()
        self.processor = AutoProcessor.from_pretrained(name)
        self.dtype = dtype
        self.dim = int(self.model.config.vision_config.projection_dim
                       if hasattr(self.model.config.vision_config, "projection_dim")
                       else self.model.config.vision_config.hidden_size)

    @torch.inference_mode()
    def embed(self, images: Sequence[Image.Image]) -> np.ndarray:
        inputs = self.processor(images=list(images), return_tensors="pt").to(self.dev)
        inputs = {k: (v.to(self.dtype) if v.is_floating_point() else v) for k, v in inputs.items()}
        feats = self.model.get_image_features(**inputs)
        if not isinstance(feats, torch.Tensor):  # newer transformers return a ModelOutput
            feats = feats.pooler_output
        out = _l2(feats.float().cpu().numpy())
        self.dim = out.shape[1]
        return out


class OllamaTextEmbedder:
    """EmbeddingGemma served by a local Ollama. Uses the model's documented task prompts."""

    def __init__(self, name: str = DEFAULT_TEXT_MODEL, host: str = OLLAMA_HOST):
        import httpx

        self.name = name
        self.client = httpx.Client(base_url=host, timeout=300)
        self.dim = 0

    def _embed(self, texts: Sequence[str]) -> np.ndarray:
        r = self.client.post("/api/embed", json={"model": self.name, "input": list(texts)})
        r.raise_for_status()
        out = _l2(np.asarray(r.json()["embeddings"], dtype=np.float32))
        self.dim = out.shape[1]
        return out

    def embed_documents(self, texts: Sequence[str], titles: Sequence[str] | None = None) -> np.ndarray:
        titles = titles or ["none"] * len(texts)
        return self._embed([f"title: {t} | text: {x}" for t, x in zip(titles, texts)])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([f"task: search result | query: {text}"])[0]


@lru_cache(maxsize=2)
def image_embedder(name: str = DEFAULT_IMAGE_MODEL) -> HFImageEmbedder:
    return HFImageEmbedder(name)


@lru_cache(maxsize=2)
def text_embedder(name: str = DEFAULT_TEXT_MODEL) -> OllamaTextEmbedder:
    return OllamaTextEmbedder(name)
