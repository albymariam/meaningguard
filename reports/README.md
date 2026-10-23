# Results

Dev numbers were used to tune the grammar and thresholds. Held-out numbers were produced once, with
nothing changed afterwards. Both sets are small, so read them as indicative.

## Does the verifier catch corrupted translations?

Positives are corrupted Hindi, negatives are correct equivalents. `digits` compares only digit strings and
currency signs. Real MT output was corrupted automatically (last digit changed, ₹ swapped for $, "नहीं"
removed), so the cause is known.

| Test | System | Precision | Recall |
|---|---|---|---|
| Dev, 24 hand-made pairs | digits baseline | 0.85 | 0.50 |
| | **verifier** | **1.00** | **1.00** |
| Dev, 95 corrupted MT outputs | digits baseline | 1.00 | 0.77 |
| | **verifier** | **1.00** | **0.99** |
| Held-out, 11 hand-made pairs | digits baseline | 0.75 | 0.43 |
| | **verifier** | **1.00** | **1.00** |
| Held-out, 38 corrupted MT outputs | digits baseline | 1.00 | 0.95 |
| | **verifier** | **1.00** | **1.00** |

The digit baseline misses every dropped negation and flags correct translations that spell numbers as words.

**Repair** (numbers and currency only): dev 71 of 73 fixed (97%), held-out 30 of 36 (83%).
Negation, direction, time and comparison errors are reported with a suggested fix, not repaired.

**Untouched MT output flagged by the verifier:** dev 7 of 178, held-out 8 of 81 (`flagged_mt` in the JSON).
By the author's reading (not checked by a native speaker): dev 3 genuine MT errors, 3 false alarms, 1 deliberate
uncertainty warning; held-out 2 genuine errors, 6 false alarms (about 7%). Real errors found include
"बावन सौ सप्ताह" for fifty-two weeks and "के माध्यम से" for a deadline.

## Emotion and sarcasm (advisory tone check)

Gold labels are the rows two annotators agree on (103 of 107, kappa 0.95).

| | Dev (70 rows) | Held-out (33 rows) |
|---|---|---|
| Emotion label accuracy | 0.75 | 0.71 |
| Sentiment vote accuracy (abstained) | 0.77 (21%) | 0.63 (39%) |
| Sentiment accuracy on the Hindi side | 0.80 (65 rows) | 0.73 (30 rows) |
| Non-sarcastic rows: label / sentiment | 0.91 / 0.93 | 0.82 / 0.76 |
| Sarcastic rows: label / sentiment | 0.00 / 0.00 (11 rows) | 0.00 / 0.00 (5 rows) |
| Sarcasm detection: precision / recall | 0.69 / 1.00 | 0.50 / 1.00 |

## Limits

- The models read the literal words: they fail on every sarcastic sentence. The irony model scores sincere
  praise ("Great job, you fixed it") like sarcastic praise, so sarcasm precision is low. Sarcasm findings are
  advisory and never fail a check.
- The verifier gave more false alarms on held-out (about 7%) than on dev (under 2%). The dev grammar was tuned on dev.
- Datasets are small. The first annotator saw proposed labels. The corrupted MT outputs are located with the
  project's own number finder, so the hand-made pairs are the independent check.
- Repair covers numbers and currency only. Only the 35 hand-made Hindi pairs were checked by a reviewer.
