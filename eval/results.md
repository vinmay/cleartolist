# ClearToList evaluation results: Run 3

Generated 2026-10-15 on a 6 GB consumer GPU. Vision model `gemma4:e4b`, image model `google/siglip2-base-patch16-224`, text model `embeddinggemma:300m`. POSSIBLE MATCH gate (chosen on the dev split): closest shortlisted recall photo has SigLIP 2 cosine >= 0.81.

Positives are held-out CPSC recall photos with simulated phone-photo degradation; the held-out photo and every copy of it are removed from the index for that query. Negatives are licensed same-category product photos from Wikimedia Commons (see the negatives manifest; audited entries marked `excluded` are left out).

## Retrieval (correct recall in top-k)

| | children | mixed | label |
|---|---|---|---|
| positives / negatives | 150 / 41 | 180 / 83 | 40 / 0 |
| image only, top-1 | 51% | 48% | 12% |
| image only, top-3 | 69% | 59% | 18% |
| text only (Gemma read), top-1 | 13% | 28% | 50% |
| text only (Gemma read), top-3 | 25% | 39% | 60% |
| fused, top-1 | 50% | 51% | 45% |
| **fused, top-3 (the shortlist)** | 67% | 64% | 60% |

## Verdicts

| | children | mixed | label |
|---|---|---|---|
| CONFIRMED count | 0 | 4 | 2 |
| CONFIRMED right | 0 | 2 | 2 |
| CONFIRMED precision | n/a | 50% | 100% |
| false-alarm rate on negatives (CONFIRMED) | 0% | 1% | n/a |
| POSSIBLE MATCH rate on negatives | 5% | 24% | n/a |
| positives shown the right recall (CONFIRMED or in a POSSIBLE MATCH shortlist) | 34% | 37% | 15% |
| POSSIBLE MATCH rate on positives | 43% | 49% | 12% |
| NO MATCH FOUND rate on positives | 57% | 49% | 82% |
| model number / UPC read and matched | 0% | 2% | 5% |
| latency per scan, median (s) | 6.5 | 7.2 | 7.1 |
| latency per scan, p90 (s) | 8.4 | 8.8 | 10.0 |

## Per category

| run / category | pos | neg | image top-3 | shortlist (fused top-3) | shown right | POSSIBLE on negatives | CONFIRMED on negatives |
|---|---|---|---|---|---|---|---|
| children/crib_bassinet_sleeper | 40 | 8 | 45% | 42% | 28% | 25% | 0% |
| children/high_chair | 13 | 1 | 69% | 69% | 46% | 0% | 0% |
| children/other_child | 40 | 6 | 80% | 75% | 35% | 0% | 0% |
| children/play_yard | 1 | 2 | 0% | 0% | 0% | 0% | 0% |
| children/stroller | 16 | 12 | 88% | 88% | 56% | 0% | 0% |
| children/toy | 40 | 12 | 78% | 75% | 28% | 0% | 0% |
| mixed/apparel | 20 | 0 | 75% | 75% | 60% | n/a | n/a |
| mixed/appliance_fixture | 20 | 0 | 50% | 60% | 35% | n/a | n/a |
| mixed/electrical_battery | 20 | 12 | 55% | 65% | 20% | 25% | 8% |
| mixed/furniture | 20 | 12 | 40% | 45% | 25% | 0% | 0% |
| mixed/heating_cooling | 20 | 12 | 55% | 70% | 50% | 25% | 0% |
| mixed/home | 20 | 12 | 55% | 55% | 20% | 8% | 0% |
| mixed/kitchen | 20 | 11 | 75% | 75% | 40% | 36% | 0% |
| mixed/sports_rec | 20 | 12 | 55% | 55% | 35% | 42% | 0% |
| mixed/tools_outdoor | 20 | 12 | 75% | 75% | 50% | 33% | 0% |
| label/crib_bassinet_sleeper | 2 | 0 | 0% | 50% | 0% | n/a | n/a |
| label/high_chair | 2 | 0 | 0% | 50% | 0% | n/a | n/a |
| label/other_child | 28 | 0 | 14% | 64% | 14% | n/a | n/a |
| label/play_yard | 1 | 0 | 0% | 100% | 0% | n/a | n/a |
| label/stroller | 2 | 0 | 50% | 50% | 50% | n/a | n/a |
| label/toy | 5 | 0 | 40% | 40% | 20% | n/a | n/a |

Categories with fewer than 10 positives are indicative only.
