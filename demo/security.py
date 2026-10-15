"""Security primitives for the hosted service: upload sanitising, worker request signing, rate limits.

Pure functions and small classes, no web framework, so they're unit-tested directly.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import secrets
import threading
import time
import warnings
from collections import deque

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 30_000_000  # ~30 MP; a decompression bomb declares far more
MAX_SIDE = 2048  # re-encoded images are downscaled to this
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

# Allowed types, identified by their leading bytes, never by filename or client-supplied content type.
_MAGIC = {"JPEG": (b"\xff\xd8\xff",), "PNG": (b"\x89PNG\r\n\x1a\n",)}


class UploadError(ValueError):
    """A rejected upload. The message is safe to show to the user (it never echoes the input)."""


def sniff(data: bytes) -> str | None:
    for fmt, sigs in _MAGIC.items():
        if any(data.startswith(s) for s in sigs):
            return fmt
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def sanitize_image(data: bytes) -> bytes:
    """Validate an uploaded photo and return a clean re-encoded JPEG with no metadata (EXIF, GPS, ICC, comments).

    Rejects: oversized files, unknown types (by magic bytes), files whose decoded format disagrees with their magic
    bytes (polyglots), truncated or malformed images, and decompression bombs. Re-encoding also drops anything
    appended after the image data.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError("Photo is too large (8 MB max).")
    fmt = sniff(data)
    if fmt is None:
        raise UploadError("Unsupported file type. Use a JPEG, PNG or WebP photo.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
            if img.format != fmt:
                raise UploadError("The file's contents don't match its type.")
            w, h = img.size
            if w * h > MAX_PIXELS or w < 16 or h < 16:
                raise UploadError("Photo dimensions are out of range.")
            img.load()
    except UploadError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise UploadError("Photo dimensions are out of range.") from e
    except Exception as e:  # malformed, truncated, unsupported variant
        raise UploadError("That file couldn't be read as a photo.") from e
    img = ImageOps.exif_transpose(img)  # keep the visible orientation, then drop the metadata
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)  # no exif=, icc_profile= or comment: nothing carried over
    return out.getvalue()


# --- worker request signing -------------------------------------------------------------------------------

SIG_WINDOW_S = 300


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sign(secret: str, method: str, path: str, body: bytes, ts: str, nonce: str) -> str:
    msg = "\n".join([method.upper(), path, ts, nonce, body_hash(body)]).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def signed_headers(secret: str, method: str, path: str, body: bytes = b"") -> dict[str, str]:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    return {"X-CTL-Timestamp": ts, "X-CTL-Nonce": nonce,
            "X-CTL-Signature": sign(secret, method, path, body, ts, nonce)}


class NonceStore:
    """Remembers nonces for the signature window so a captured request can't be replayed."""

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def use(self, nonce: str, now: float) -> bool:
        with self._lock:
            for k in [k for k, exp in self._seen.items() if exp < now]:
                del self._seen[k]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now + 2 * SIG_WINDOW_S
            return True


def verify(secret: str | None, method: str, path: str, body: bytes, headers: dict[str, str],
           nonces: NonceStore, now: float | None = None) -> bool:
    """Constant-time check of an HMAC-signed request. False on any missing, stale, replayed or wrong signature."""
    if not secret or len(secret) < 32:
        return False  # worker access is off unless a strong secret is configured
    ts, nonce, sig = (headers.get("x-ctl-timestamp", ""), headers.get("x-ctl-nonce", ""),
                      headers.get("x-ctl-signature", ""))
    if not (ts.isdigit() and 16 <= len(nonce) <= 64 and len(sig) == 64):
        return False
    now = time.time() if now is None else now
    if abs(now - int(ts)) > SIG_WINDOW_S:
        return False
    if not hmac.compare_digest(sign(secret, method, path, body, ts, nonce), sig):
        return False
    return nonces.use(nonce, now)


def code_matches(supplied: str, allowed: list[str]) -> bool:
    """Constant-time membership check for invite codes."""
    got = hashlib.sha256(supplied.strip().encode()).digest()
    ok = False
    for code in allowed:
        ok |= hmac.compare_digest(got, hashlib.sha256(code.encode()).digest())
    return ok


# --- rate limiting and anonymous client keys ---------------------------------------------------------------

_SALT = os.urandom(16)  # per process: client keys can't be linked across restarts or reversed to an IP


def client_key(forwarded_for: str | None, peer: str | None) -> str:
    """An anonymous per-client key. Raw IPs are never stored or logged.

    Behind Render's proxy the client address is the last X-Forwarded-For entry (the one the proxy appended;
    earlier entries are client-controlled).
    """
    ip = (forwarded_for or "").split(",")[-1].strip() or (peer or "unknown")
    return hmac.new(_SALT, ip.encode(), hashlib.sha256).hexdigest()[:24]


class RateLimiter:
    """Sliding-window limit: at most `limit` events per `window` seconds per key."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory under a flood of distinct keys
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[k]
            return True
