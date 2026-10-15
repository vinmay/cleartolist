# Gate and verifier choice (dev split only)

Dev rows: 153 positives, 71 negatives. Objective and rule: see `eval/gate.py`.

## POSSIBLE MATCH gate

Chosen: closest shortlisted recall photo SigLIP 2 cosine >= **0.81**; label photos whose words led the search do **not** get POSSIBLE MATCH.

On dev: positives shown the right recall 31%, negatives prompted (POSSIBLE or CONFIRMED) 7%, J = 0.24.

| threshold | text-led on | shown right (pos) | prompted (neg) | J |
|---|---|---|---|---|
| 0.50 | no | 61% | 100% | -0.39 |
| 0.55 | no | 61% | 94% | -0.34 |
| 0.60 | no | 61% | 87% | -0.27 |
| 0.65 | no | 60% | 79% | -0.19 |
| 0.70 | no | 57% | 54% | 0.03 |
| 0.75 | no | 48% | 32% | 0.16 |
| 0.80 | no | 35% | 13% | 0.23 |
| 0.81 | no | 31% | 7% | 0.24 |
| 0.85 | no | 14% | 6% | 0.09 |
| 0.90 | no | 3% | 1% | 0.01 |
| 0.95 | no | 2% | 1% | 0.01 |
| 0.50 | yes | 62% | 100% | -0.38 |
| 0.55 | yes | 62% | 97% | -0.35 |
| 0.60 | yes | 62% | 94% | -0.32 |
| 0.65 | yes | 62% | 86% | -0.24 |
| 0.70 | yes | 60% | 62% | -0.02 |
| 0.75 | yes | 54% | 41% | 0.13 |
| 0.80 | yes | 42% | 23% | 0.20 |
| 0.81 | yes | 39% | 18% | 0.21 |
| 0.85 | yes | 24% | 17% | 0.07 |
| 0.90 | yes | 13% | 13% | 0.00 |
| 0.95 | yes | 12% | 13% | -0.00 |

## Verifier

Top-3 shortlist accuracy on dev positives: fused **95/153**, with the verifier promoting its pick from the top 5: **96/153**. The verifier improves top-3 and stays in the decision path.

| run | positives | fused top-3 | verifier top-3 |
|---|---|---|---|
| children | 56 | 35 | 35 |
| mixed | 72 | 48 | 49 |
| label | 25 | 12 | 12 |
## Decision (owner, 2026-10-15)

The verifier's dev gain is one query (95 -> 96 of 153: 1 win, 0 losses) and it adds a median 23 s per scan
(12.7 s -> about 35 s on a 6 GB consumer GPU). It is **dropped from the decision path**: a one-query gain is not worth
nearly tripling scan time. Run 3 uses the fused top-3 shortlist only, with the gate above.
