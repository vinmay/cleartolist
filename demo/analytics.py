"""Privacy-first usage counts: daily totals of named events, nothing else.

No cookies, no IP addresses, no user agents, no referrer headers, no per-person or per-session data. Counts are
buffered in memory and added to the database's daily totals about once a minute (and at shutdown).
"""

from __future__ import annotations

import threading
import time
from collections import Counter

TIERS = {"CONFIRMED": "confirmed", "POSSIBLE MATCH": "possible", "NO MATCH FOUND": "no_match"}
REFS = ("dev", "reddit", "facebook", "direct", "other")
METRICS = (
    {"page_view", "sample_scan", "real_scan", "batch_scan", "lookup", "invite_request", "alert_signup",
     "copy_link", "ask_seller"}
    | {f"ref_{r}" for r in REFS}
    | {f"verdict_{t}" for t in TIERS.values()}
    | {f"helpful_{a}_{t}" for a in ("yes", "no") for t in TIERS.values()}
)


def ref_metric(ref: str | None) -> str:
    """The ?ref= channel tag, reduced to the allowlist: no tag counts as direct, anything unknown as other."""
    if not ref:
        return "ref_direct"
    r = ref.strip().lower()
    return f"ref_{r}" if r in REFS else "ref_other"


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class Counts:
    def __init__(self, store) -> None:
        self.store = store
        self.pending: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def add(self, metric: str, n: int = 1) -> None:
        if metric not in METRICS:  # only known names are ever stored
            raise ValueError(metric)
        with self._lock:
            self.pending[(today(), metric)] += n

    def flush(self) -> None:
        with self._lock:
            rows, self.pending = [(d, m, n) for (d, m), n in self.pending.items()], Counter()
        if not rows:
            return
        if not self.store.available:
            with self._lock:  # keep them in memory when there's no database (local runs)
                for d, m, n in rows:
                    self.pending[(d, m)] += n
            return
        try:
            self.store.add_counts(rows)
        except Exception:
            with self._lock:  # try again on the next flush
                for d, m, n in rows:
                    self.pending[(d, m)] += n

    def totals(self, since_day: str) -> dict[str, dict[str, int]]:
        """{day: {metric: count}} from the database plus what hasn't been flushed yet."""
        out: dict[str, dict[str, int]] = {}
        rows = self.store.counts(since_day) if self.store.available else []
        with self._lock:
            rows = list(rows) + [(d, m, n) for (d, m), n in self.pending.items() if d >= since_day]
        for d, m, n in rows:
            out.setdefault(d, {})
            out[d][m] = out[d].get(m, 0) + int(n)
        return out
