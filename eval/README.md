# Full evaluation

How ClearToList was measured, what changed between runs, and why. The verdicts in the app are **Recalled**, **Check the label** and **No match found**; the code and the result files call them CONFIRMED, POSSIBLE MATCH and NO MATCH FOUND.

## Setup

- **Positives:** held-out CPSC recall photos with simulated phone-photo damage (crop, rotation, dimming, blur). The query photo and every copy or near-duplicate of it (SigLIP 2 cosine ≥ 0.95) are removed from the index for that query, so a query can only score through *other* photos of the same recall. Of 772 children's product recalls, 432 have two or more distinct photos, which is what makes a held-out set possible.
- **Negatives:** licensed, same-category product photos from Wikimedia Commons ([manifest](negatives_manifest.json)), checked by SigLIP zero-shot and by Gemma for the right main subject. An audit removed two that might actually be recalled (a Rival kettle of the recalled design and a Nuna high chair). A Graco play yard stayed, because only its inclined sleeper accessory was recalled and that isn't in the photo.
- **Splits:** rules were chosen on a dev split that shares no recall and no photo with the test set. The test set was run once.
- **Choosing the reader:** Gemma 4 E2B and E4B were compared on 30 held-out photos, scored on whether the read maps to the recall's product category. The first prompt scored 6/30 (E2B) and 8/30 (E4B), because Gemma answered "baby product" rather than "bassinet". Asking for a specific item type brought both to 15/30, and E4B read faster (median 3.6 s vs 6.2 s), so E4B is the reader.

## Run 3 (the reported run)

| | Children's products | Other CPSC categories | Label close-ups |
|---|---|---|---|
| positives / negatives | 150 / 41 | 180 / 83 | 40 / 0 |
| **right recall shown to the user** | **34%** | **37%** | **15%** |
| right recall shown, Run 3b (grey "closest recalls to check" for children's products) | 63% | 38% | 57% |
| lookalikes shown any recall: Run 3 → Run 3b | 5% → 98% | 24% → 37% | n/a |
| right recall in the top-3 shortlist (before the gate) | 67% | 64% | 60% |
| CONFIRMED: count, right (with the brand check) | 0 | 3, 2 right | 2, 2 right |
| false alarms on lookalikes (CONFIRMED) | 0% | 0% | n/a |
| POSSIBLE MATCH on lookalikes | 5% | 24% | n/a |
| median scan time | 6.5 s | 7.2 s | 7.1 s |

Detailed tables: [Run 3 results](results.md), [gate and verifier choice on dev](gate.md), [display policy (Run 3b)](display_policy.md), [brand-check fix](confirm_fix.md).

![Correct recall in the top 3](ablation.png)

## Other categories

The same scan finds the right recall about as well for heaters, chargers, furniture, kitchen goods, tools and sports gear (64% top 3, 37% shown). The weak spot is noise: ordinary lookalikes in those categories get a POSSIBLE MATCH 24% of the time, against 5% for children's products. It's worst for sports gear (42%), kitchen goods (36%) and tools (33%), where recalled products look like everyday ones. One similarity threshold was chosen across all dev categories, so per-category thresholds are the next fix. None of it is an alarm: after the brand check, CONFIRMED raised no false alarm on 83 other-category lookalikes.

## Post-test display change (Run 3b)

For children's products, a bare NO MATCH FOUND now comes with the three closest recalls in grey, worded as suggestions to check, not matches. The bar was fixed first (at least +15 points of "right recall shown" on dev). It went from 25% to 59% on dev, so it was adopted and applied once to the test rows: 34% → 63%. The price is that 98% of children's lookalikes also get three suggestions, which is why they're grey and never called a match.

## Post-test fix: the brand check

Run 3 first had 2 wrong CONFIRMED verdicts out of 4 in the other-categories run:

- A camera battery label listing compatible cameras ("EOS 100D") matched a fire extinguisher's model number "100D".
- An Intertex blower's model number (VP-33) appears in two recalls of the same product line, so the other one was picked.

On the dev split, vetoing a code match when the brand read off the item contradicts the recall removed both wrong CONFIRMED verdicts there and kept the right ones. Applied once to the saved test rows, it removed the battery false alarm (4 → 3 CONFIRMED, 2 right). The shared model number stays a disclosed miss.

## Runs 1 and 2

- **Run 1** ([results](results_run1.md)) used a Gemma verifier to pick among the candidates and called its high-confidence picks LIKELY. It was right about half the time and raised alarms on 31% of children's lookalikes.
- **Run 2** ([results](results_run2.md)): error analysis on Run 1 found two bugs (text-led fusion on photos where no words were read, and a fallback looking at the wrong candidate). They were fixed and the same queries re-run, so Run 2's gain over Run 1 is optimistic.
- **Run 3:** the verifier was taken out of the alarm path, the gate was chosen on the dev split only, and the test ran once. On dev, the verifier added one query of top 3 (95 to 96 of 153) for about 23 s more per scan, so it was dropped. Fine-tuning it was planned and set aside for the same reason.

## Limits

The positives are official photos made to look like phone shots, not real thrift store photos. The Commons negatives aren't verified as non-recalled. Play yards (1 positive) and high chairs (13) are too small to conclude anything. A harness for real photos is ready in [`owner_photos/`](owner_photos/).
