# Post-test fix: brand check on CONFIRMED

Run 3 had 2 wrong CONFIRMED verdicts out of 4 in the mixed run. Causes: a short token on a battery label ("EOS 100D", a compatible camera) matched an unrelated recall's model number; and a model number (Intertex VP-33) that appears in two recalls of the same product line, so the code was right but the recall picked was the other one.

Rule chosen on the dev split: when a brand is read off the item and the code-matched recall's manufacturer and title don't mention it, the code match doesn't confirm (the verdict falls back to POSSIBLE MATCH or NO MATCH FOUND by the photo gate). On dev it removed both wrong CONFIRMED verdicts and kept all correct ones. It was then applied to the saved Run 3 rows: only the verdict step is recomputed.

### Dev split (where the rule was chosen)

| | children before | children after | mixed before | mixed after | label before | label after |
|---|---|---|---|---|---|---|
| CONFIRMED | 1 | 0 | 1 | 0 | 3 | 3 |
| CONFIRMED right | 0 | 0 | 0 | 0 | 3 | 3 |
| CONFIRMED on negatives | 0 | 0 | 1 | 0 | 0 | 0 |

- children pos (true recall 9375): CONFIRMED (recall 8751) -> NO MATCH FOUND; brand read: "Kidoozie"
- mixed neg (true recall None): CONFIRMED (recall 8047) -> NO MATCH FOUND; brand read: "SKYRC"

### Test (Run 3 rows, recomputed once)

| | children before | children after | mixed before | mixed after | label before | label after |
|---|---|---|---|---|---|---|
| CONFIRMED | 0 | 0 | 4 | 3 | 2 | 2 |
| CONFIRMED right | 0 | 0 | 2 | 2 | 2 | 2 |
| CONFIRMED on negatives | 0 | 0 | 1 | 0 | 0 | 0 |

- mixed neg (true recall None): CONFIRMED (recall 8170) -> NO MATCH FOUND; brand read: "RAYLAB"

The shared-model-number case (VP-33) is not fixed by this rule: the item does carry a recalled model number, but the number belongs to two recalls. No rule for it could be validated on dev (no such case there).
