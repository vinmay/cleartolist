"""Reciprocal rank fusion and exact-match boosting. Pure functions, no models."""

from __future__ import annotations

from dataclasses import dataclass, field

RRF_K = 60


@dataclass
class Candidate:
    recall_id: int
    score: float = 0.0
    sources: dict[str, int] = field(default_factory=dict)  # source name -> 1-based rank in that list
    exact: list[str] = field(default_factory=list)  # e.g. ["model:AB123", "upc:0123..."]
    best_image: str | None = None


def rrf(ranked_lists: dict[str, list[int]], k: int = RRF_K, weights: dict[str, float] | None = None) -> list[Candidate]:
    """Fuse several ranked lists of recall ids. Missing from a list = contributes nothing."""
    weights = weights or {}
    cands: dict[int, Candidate] = {}
    for name, ids in ranked_lists.items():
        w = weights.get(name, 1.0)
        for rank, rid in enumerate(ids, start=1):
            c = cands.setdefault(rid, Candidate(rid))
            if name in c.sources:
                continue
            c.sources[name] = rank
            c.score += w / (k + rank)
    return sorted(cands.values(), key=lambda c: (-c.score, c.recall_id))


def boost_exact(cands: list[Candidate], exact_hits: dict[int, list[str]]) -> list[Candidate]:
    """Put recalls with an exact model-number or UPC hit first, keeping fused order within each group."""
    by_id = {c.recall_id: c for c in cands}
    for rid, reasons in exact_hits.items():
        c = by_id.setdefault(rid, Candidate(rid))
        c.exact = sorted(set(c.exact) | set(reasons))
    strong = [c for c in by_id.values() if any(r.startswith(("model:", "upc:")) for r in c.exact)]
    rest = [c for c in by_id.values() if c not in strong]
    key = lambda c: (-c.score, c.recall_id)  # noqa: E731
    return sorted(strong, key=key) + sorted(rest, key=key)
