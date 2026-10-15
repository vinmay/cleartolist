"""Choose the POSSIBLE MATCH gate and decide on the verifier, from dev-split rows only.

    uv run python -m eval.gate            # reads eval/work/dev_rows.jsonl, writes eval/gate.md

Objective, fixed before looking at any dev result: maximise Youden's J =
  (share of dev positives shown the right recall: CONFIRMED, or in a POSSIBLE MATCH shortlist)
  - (share of dev negatives that get POSSIBLE MATCH or CONFIRMED),
pooled over the children's, mixed and label runs. Ties go to the higher threshold (fewer prompts).

The verifier (Gemma forced choice over the fused top 5, its pick moved to the front) stays in the decision
path only if it raises top-3 shortlist accuracy on the dev positives; otherwise it is dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.dataset import WORK
from verify.verdict import Verdict, decide

ROWS = WORK / "dev_rows.jsonl"
OUT = Path("eval/gate.md")


def outcome(r: dict, sim: float, text_led_on: bool) -> tuple[str, bool]:
    sl = r["shortlist"]
    v, _ = decide([c["exact"] for c in sl], [c["image_score"] for c in sl], r["text_led"], sim, text_led_on)
    shown = v != Verdict.NO_MATCH and r["recall_id"] in [c["recall_id"] for c in sl]
    return v.value, shown


def main() -> None:
    rows = [json.loads(x) for x in ROWS.read_text().splitlines()]
    pos = [r for r in rows if r["label"] == "pos"]
    neg = [r for r in rows if r["label"] == "neg"]
    if not neg:
        raise SystemExit("dev split has no negatives yet")
    grid = []
    for text_led_on in (False, True):
        for i in range(50, 96):
            sim = i / 100
            tp = sum(outcome(r, sim, text_led_on)[1] for r in pos) / len(pos)
            fp = sum(outcome(r, sim, text_led_on)[0] != Verdict.NO_MATCH.value for r in neg) / len(neg)
            grid.append((round(tp - fp, 4), sim, text_led_on, tp, fp))
    best = max(grid, key=lambda g: (g[0], g[1]))
    J, sim, text_led_on, tp, fp = best

    vr = [r for r in pos if "verifier_ids" in r]
    base = sum(r["recall_id"] in [c["recall_id"] for c in r["shortlist"]] for r in vr)
    ver = sum(r["recall_id"] in r["verifier_ids"] for r in vr)
    keep_verifier = ver > base
    by_run = {}
    for run in ("children", "mixed", "label"):
        rr = [r for r in vr if r["run"] == run]
        if rr:
            by_run[run] = (len(rr), sum(r["recall_id"] in [c["recall_id"] for c in r["shortlist"]] for r in rr),
                           sum(r["recall_id"] in r["verifier_ids"] for r in rr))

    L = ["# Gate and verifier choice (dev split only)", "",
         f"Dev rows: {len(pos)} positives, {len(neg)} negatives. Objective and rule: see `eval/gate.py`.", "",
         "## POSSIBLE MATCH gate", "",
         f"Chosen: closest shortlisted recall photo SigLIP 2 cosine >= **{sim:.2f}**; label photos whose words led "
         f"the search {'also' if text_led_on else 'do **not**'} get POSSIBLE MATCH.", "",
         f"On dev: positives shown the right recall {tp:.0%}, negatives prompted (POSSIBLE or CONFIRMED) {fp:.0%}, "
         f"J = {J:.2f}.", "",
         "| threshold | text-led on | shown right (pos) | prompted (neg) | J |", "|---|---|---|---|---|"]
    for g in sorted(grid, key=lambda g: (g[2], g[1])):
        if round(g[1] * 100) % 5 == 0 or g[1] == sim:
            L.append(f"| {g[1]:.2f} | {'yes' if g[2] else 'no'} | {g[3]:.0%} | {g[4]:.0%} | {g[0]:.2f} |")
    L += ["", "## Verifier", "",
          f"Top-3 shortlist accuracy on dev positives: fused **{base}/{len(vr)}**, with the verifier promoting "
          f"its pick from the top 5: **{ver}/{len(vr)}**. "
          + ("The verifier improves top-3 and stays in the decision path." if keep_verifier else
             "The verifier does not improve top-3, so it is dropped from the decision path."), "",
          "| run | positives | fused top-3 | verifier top-3 |", "|---|---|---|---|"]
    for run, (n, b, v) in by_run.items():
        L.append(f"| {run} | {n} | {b} | {v} |")
    L.append("")
    OUT.write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
