# ClearToList evaluation results: frozen test run (before error analysis)

Generated 2026-10-15 on a 6 GB consumer GPU. Vision model `gemma4:e4b`, image model `google/siglip2-base-patch16-224`, text model `embeddinggemma:300m`.

Positives are held-out CPSC recall photos with simulated phone-photo degradation; the held-out photo and every copy of it are removed from the index for that query. Negatives are licensed same-category product photos from Wikimedia Commons (see `eval/negatives_manifest.json`).

## Retrieval ablation (correct recall in top-k)

| | children | mixed | label |
|---|---|---|---|
| positives / negatives | 150 / 42 | 180 / 84 | 40 / 0 |
| image only, top-1 | 51% | 48% | 12% |
| image only, top-3 | 69% | 59% | 18% |
| text only (Gemma read), top-1 | 13% | 27% | 48% |
| text only (Gemma read), top-3 | 27% | 39% | 55% |
| fused, top-1 | 47% | 47% | 42% |
| fused, top-3 | 62% | 62% | 55% |
| fused + verifier, top-1 | 37% | 36% | 45% |

## Verdicts (fused + verifier)

| | children | mixed | label |
|---|---|---|---|
| CONFIRMED precision | n/a | 75% | 100% |
| CONFIRMED count | 0 | 4 | 2 |
| LIKELY precision | 48% | 49% | 80% |
| LIKELY count | 58 | 67 | 15 |
| positives flagged with the right recall | 19% | 20% | 35% |
| false-alarm rate on negatives (CONFIRMED or LIKELY) | 31% | 12% | n/a |
| UNSURE rate, positives | 35% | 34% | 18% |
| UNSURE rate, negatives | 5% | 26% | n/a |
| model number / UPC read and matched | 0% | 2% | 8% |
| latency per scan, median (s) | 24.0 | 24.9 | 28.5 |
| latency per scan, p90 (s) | 32.5 | 34.1 | 38.6 |

## Per category

| run / category | pos | neg | image top-3 | fused top-3 | flagged right | false alarms |
|---|---|---|---|---|---|---|
| children/crib_bassinet_sleeper | 40 | 8 | 45% | 40% | 12% | 25% |
| children/high_chair | 13 | 2 | 69% | 69% | 15% | 0% |
| children/other_child | 40 | 6 | 80% | 68% | 12% | 17% |
| children/play_yard | 1 | 2 | 0% | 0% | 0% | 50% |
| children/stroller | 16 | 12 | 88% | 88% | 25% | 58% |
| children/toy | 40 | 12 | 78% | 68% | 30% | 17% |
| mixed/apparel | 20 | 0 | 75% | 75% | 20% | n/a |
| mixed/appliance_fixture | 20 | 0 | 50% | 55% | 25% | n/a |
| mixed/electrical_battery | 20 | 12 | 55% | 60% | 20% | 17% |
| mixed/furniture | 20 | 12 | 40% | 45% | 0% | 0% |
| mixed/heating_cooling | 20 | 12 | 55% | 70% | 25% | 17% |
| mixed/home | 20 | 12 | 55% | 55% | 15% | 0% |
| mixed/kitchen | 20 | 12 | 75% | 75% | 35% | 8% |
| mixed/sports_rec | 20 | 12 | 55% | 50% | 25% | 0% |
| mixed/tools_outdoor | 20 | 12 | 75% | 75% | 15% | 42% |
| label/crib_bassinet_sleeper | 2 | 0 | 0% | 50% | 0% | n/a |
| label/high_chair | 2 | 0 | 0% | 0% | 0% | n/a |
| label/other_child | 28 | 0 | 14% | 61% | 39% | n/a |
| label/play_yard | 1 | 0 | 0% | 100% | 100% | n/a |
| label/stroller | 2 | 0 | 50% | 50% | 50% | n/a |
| label/toy | 5 | 0 | 40% | 40% | 20% | n/a |

Categories with fewer than 10 positives are indicative only.
