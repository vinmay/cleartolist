# Display policy: closest recalls to check (Run 3b)

Policy: when neither CONFIRMED nor POSSIBLE MATCH triggers and the item is a children's product (Gemma marks it as one, or its closest recall is a children's recall), show the three closest recalls in grey as "closest recalls to check": suggestions, not matches, with no alarm. The verdict stays NO MATCH FOUND.

Adoption rule, fixed before looking: right recall shown must rise by at least 15 points on dev (children's products). On dev it rose 34 points, so the policy is adopted.

### Dev split (decision)

| run | positives / negatives | right recall shown: current → new | lookalikes shown recalls (any kind): current → new | false alarms (CONFIRMED on lookalikes) |
|---|---|---|---|---|
| children | 56 / 29 | 25% → 59% | 7% → 93% | 0% |
| mixed | 72 / 42 | 42% → 43% | 5% → 7% | 0% |
| label | 25 / 0 | 16% → 44% | n/a → n/a | n/a |

### Test: Run 3 (current policy) → Run 3b (new policy), computed once from the saved Run 3 rows

| run | positives / negatives | right recall shown: current → new | lookalikes shown recalls (any kind): current → new | false alarms (CONFIRMED on lookalikes) |
|---|---|---|---|---|
| children | 150 / 41 | 34% → 63% | 5% → 98% | 0% |
| mixed | 180 / 83 | 37% → 38% | 24% → 37% | 0% |
| label | 40 / 0 | 15% → 57% | n/a → n/a | n/a |

"Lookalikes shown recalls" counts any recall shown for a negative (CONFIRMED, POSSIBLE MATCH or closest recalls). Closest recalls are not an alarm: the page says they are suggestions, not matches.
