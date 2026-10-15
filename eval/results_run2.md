# ClearToList evaluation results: Run 2 (post-hoc fixes, LIKELY/UNSURE verdicts)

Generated 2026-10-15 on a 6 GB consumer GPU. Vision model `gemma4:e4b`, image model `google/siglip2-base-patch16-224`, text model `embeddinggemma:300m`.

This is the post-hoc run. The frozen test run (`eval/results_run1.md`) came first; error analysis on it found two bugs (text-led fusion on photos where nothing was read, and an UNSURE fallback that looked only at the first candidate), fixed in commit 7bff2ed. This run reuses the same Gemma reads, so only fusion and verdicts changed. Because the fixes were found on the test set, treat the gap between the two runs as optimistic.

Positives are held-out CPSC recall photos with simulated phone-photo degradation; the held-out photo and every copy of it are removed from the index for that query. Negatives are licensed same-category product photos from Wikimedia Commons (see `eval/negatives_manifest.json`).

## Retrieval ablation (correct recall in top-k)

| | children | mixed | label |
|---|---|---|---|
| positives / negatives | 150 / 42 | 180 / 84 | 40 / 0 |
| image only, top-1 | 51% | 48% | 12% |
| image only, top-3 | 69% | 59% | 18% |
| text only (Gemma read), top-1 | 13% | 27% | 48% |
| text only (Gemma read), top-3 | 27% | 39% | 55% |
| fused, top-1 | 50% | 51% | 40% |
| fused, top-3 | 67% | 64% | 55% |
| fused + verifier, top-1 | 41% | 41% | 42% |

## Verdicts (fused + verifier)

| | children | mixed | label |
|---|---|---|---|
| CONFIRMED precision | n/a | 75% | 100% |
| CONFIRMED count | 0 | 4 | 2 |
| LIKELY precision | 51% | 55% | 75% |
| LIKELY count | 61 | 67 | 16 |
| positives flagged with the right recall | 21% | 22% | 35% |
| false-alarm rate on negatives (CONFIRMED or LIKELY) | 31% | 10% | n/a |
| UNSURE rate, positives | 37% | 37% | 12% |
| UNSURE rate, negatives | 10% | 26% | n/a |
| model number / UPC read and matched | 0% | 2% | 8% |
| latency per scan, median (s) | 27.9 | 30.0 | 31.2 |
| latency per scan, p90 (s) | 36.2 | 40.4 | 44.7 |

## Per category

| run / category | pos | neg | image top-3 | fused top-3 | flagged right | false alarms |
|---|---|---|---|---|---|---|
| children/crib_bassinet_sleeper | 40 | 8 | 45% | 45% | 15% | 38% |
| children/high_chair | 13 | 2 | 69% | 69% | 15% | 0% |
| children/other_child | 40 | 6 | 80% | 75% | 12% | 33% |
| children/play_yard | 1 | 2 | 0% | 0% | 0% | 50% |
| children/stroller | 16 | 12 | 88% | 88% | 25% | 42% |
| children/toy | 40 | 12 | 78% | 72% | 35% | 17% |
| mixed/apparel | 20 | 0 | 75% | 80% | 20% | n/a |
| mixed/appliance_fixture | 20 | 0 | 50% | 60% | 30% | n/a |
| mixed/electrical_battery | 20 | 12 | 55% | 60% | 25% | 8% |
| mixed/furniture | 20 | 12 | 40% | 45% | 5% | 0% |
| mixed/heating_cooling | 20 | 12 | 55% | 70% | 30% | 17% |
| mixed/home | 20 | 12 | 55% | 55% | 20% | 0% |
| mixed/kitchen | 20 | 12 | 75% | 75% | 35% | 8% |
| mixed/sports_rec | 20 | 12 | 55% | 55% | 20% | 0% |
| mixed/tools_outdoor | 20 | 12 | 75% | 80% | 15% | 33% |
| label/crib_bassinet_sleeper | 2 | 0 | 0% | 50% | 0% | n/a |
| label/high_chair | 2 | 0 | 0% | 0% | 0% | n/a |
| label/other_child | 28 | 0 | 14% | 61% | 39% | n/a |
| label/play_yard | 1 | 0 | 0% | 100% | 100% | n/a |
| label/stroller | 2 | 0 | 50% | 50% | 50% | n/a |
| label/toy | 5 | 0 | 40% | 40% | 20% | n/a |

Categories with fewer than 10 positives are indicative only.
