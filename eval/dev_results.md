# ClearToList evaluation results (dev split)

Generated 2026-10-15 on a 6 GB consumer GPU. Vision model `gemma4:e4b`, image model `google/siglip2-base-patch16-224`, text model `embeddinggemma:300m`. POSSIBLE MATCH gate (chosen on the dev split): closest shortlisted recall photo has SigLIP 2 cosine >= 0.7, or the photo is a label whose words led the search.

Positives are held-out CPSC recall photos with simulated phone-photo degradation; the held-out photo and every copy of it are removed from the index for that query. Negatives are licensed same-category product photos from Wikimedia Commons (see the negatives manifest; audited entries marked `excluded` are left out).

## Retrieval (correct recall in top-k)

| | children | mixed | label |
|---|---|---|---|
| positives / negatives | 56 / 29 | 72 / 42 | 25 / 0 |
| image only, top-1 | 54% | 38% | 16% |
| image only, top-3 | 68% | 68% | 16% |
| text only (Gemma read), top-1 | 23% | 24% | 44% |
| text only (Gemma read), top-3 | 32% | 33% | 52% |
| fused, top-1 | 48% | 35% | 36% |
| **fused, top-3 (the shortlist)** | 62% | 67% | 48% |
| verifier promotes 1 of top 5, top-3 | 62% | 68% | 48% |

## Verdicts

| | children | mixed | label |
|---|---|---|---|
| CONFIRMED count | 1 | 1 | 3 |
| CONFIRMED right | 0 | 0 | 3 |
| CONFIRMED precision | 0% | 0% | 100% |
| false-alarm rate on negatives (CONFIRMED) | 0% | 2% | n/a |
| POSSIBLE MATCH rate on negatives | 41% | 74% | n/a |
| positives shown the right recall (CONFIRMED or in a POSSIBLE MATCH shortlist) | 59% | 65% | 48% |
| POSSIBLE MATCH rate on positives | 93% | 99% | 84% |
| NO MATCH FOUND rate on positives | 5% | 1% | 4% |
| model number / UPC read and matched | 2% | 0% | 12% |
| latency per scan, median (s) | 10.9 | 15.5 | 10.1 |
| latency per scan, p90 (s) | 19.4 | 19.9 | 14.9 |

## Per category

| run / category | pos | neg | image top-3 | shortlist (fused top-3) | shown right | POSSIBLE on negatives | CONFIRMED on negatives |
|---|---|---|---|---|---|---|---|
| children/crib_bassinet_sleeper | 6 | 6 | 33% | 33% | 33% | 67% | 0% |
| children/high_chair | 0 | 6 | n/a | n/a | n/a | 33% | 0% |
| children/other_child | 25 | 3 | 64% | 56% | 52% | 67% | 0% |
| children/play_yard | 0 | 2 | n/a | n/a | n/a | 0% | 0% |
| children/stroller | 0 | 6 | n/a | n/a | n/a | 33% | 0% |
| children/toy | 25 | 6 | 80% | 76% | 72% | 33% | 0% |
| mixed/apparel | 8 | 0 | 75% | 75% | 75% | n/a | n/a |
| mixed/appliance_fixture | 8 | 0 | 50% | 50% | 50% | n/a | n/a |
| mixed/electrical_battery | 8 | 6 | 50% | 38% | 38% | 83% | 17% |
| mixed/furniture | 8 | 6 | 38% | 38% | 38% | 50% | 0% |
| mixed/heating_cooling | 8 | 6 | 62% | 75% | 75% | 100% | 0% |
| mixed/home | 8 | 6 | 75% | 75% | 62% | 67% | 0% |
| mixed/kitchen | 8 | 6 | 75% | 75% | 75% | 100% | 0% |
| mixed/sports_rec | 8 | 6 | 100% | 100% | 100% | 33% | 0% |
| mixed/tools_outdoor | 8 | 6 | 88% | 75% | 75% | 83% | 0% |
| label/crib_bassinet_sleeper | 1 | 0 | 0% | 100% | 100% | n/a | n/a |
| label/other_child | 22 | 0 | 14% | 41% | 41% | n/a | n/a |
| label/stroller | 1 | 0 | 100% | 100% | 100% | n/a | n/a |
| label/toy | 1 | 0 | 0% | 100% | 100% | n/a | n/a |

Categories with fewer than 10 positives are indicative only.
