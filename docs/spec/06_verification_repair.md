# 06 Verification and repair (v1)

## 1. Alignment
Clauses are aligned by index when both sides have the same number of clauses. If the counts differ,
the verifier aligns by fingerprints across the whole sentence and reports `CLAUSE_COUNT_CHANGED`
(warning). Facts are then compared as multisets of fingerprints per aligned unit.

## 2. Fact comparison
For each source fact there are four outcomes:
| Outcome | Condition | Severity |
|---|---|---|
| `MATCH` | a target fact with the same fingerprint exists | none |
| `CHANGED` | a target fact of the same `kind` and `role` exists with different values | error |
| `MISSING` | no target fact of that kind and role | error |
| `ADDED` | a target fact has no source counterpart | warning |

`CHANGED` carries a code: `NUMBER_CHANGED`, `CURRENCY_CHANGED`, `CURRENCY_MISSING`,
`DIRECTION_CHANGED`, `COMPARISON_CHANGED`, `TIMEREL_CHANGED`, `DATE_CHANGED`, `TIME_CHANGED`.
Polarity: a clause whose `polarity` differs gives `NEGATION_DROPPED` (source NEG, target POS) or
`NEGATION_ADDED` (source POS, target NEG). Both are errors. `DOUBLE_NEG` on either side lowers the
result to warning `NEGATION_UNCERTAIN`.

Finding codes: `FACT_MISSING`, `FACT_ADDED`, `NUMBER_CHANGED`, `CURRENCY_CHANGED` (both sides have a
currency, codes differ), `CURRENCY_MISSING` (source has one, target has none), `CURRENCY_ADDED`
(warning; source has none, target has one), `DIRECTION_CHANGED`, `COMPARISON_CHANGED`,
`TIMEREL_CHANGED`, `DATE_CHANGED`, `TIME_CHANGED`, `TIME_PERIOD_MISSING` (warning),
`DATE_PRECISION_CHANGED` (warning), `PRECISION_CHANGED` (warning), `VAGUE_QUANTITY_CHANGED`,
`NEGATION_DROPPED`, `NEGATION_ADDED`, `NEGATION_UNCERTAIN`, `CLAUSE_COUNT_CHANGED` (warning),
`AMBIGUOUS_NUMBER` (warning), `UNVERIFIED` (info), `REPAIR_REVERTED`.
When one source fact differs from one target fact in several ways, the most specific code wins in
this order: `DIRECTION_CHANGED`, `CURRENCY_*`, `COMPARISON_CHANGED`, `NUMBER_CHANGED`.

## 3. Confidence and tier
- Tier 1 on both sides: findings are reported as is.
- Tier 2 on either side: findings are reported, and `MISSING` becomes `UNVERIFIED` (info) because the
  parser may have skipped the fact.
- Tier 3 on either side: only number, currency and clause-polarity checks run; every result carries
  `confidence: low` and the UI label "basic check".
- Lexer flags `COMMA_DECIMAL_SUSPECT`, `DOT_THOUSANDS_SUSPECT`, `MIXED_DIGIT_SCRIPT` on a number
  downgrade its `CHANGED` to `AMBIGUOUS_NUMBER` (warning) and block repair for that fact.

## 4. Tone comparison (uses the Emotion record)
| Source | Target | Result |
|---|---|---|
| `positive` | `negative` (or reverse) | `TONE_POLARITY_FLIPPED` (warning) |
| `neutral` | `positive`/`negative` with confidence >= 0.7 | `TONE_INTENSIFIED` (info) |
| `possible_sarcasm` = true | target sentiment `positive` | `SARCASM_POSSIBLY_LOST` (warning) |
| either side `uncertain` | | `TONE_UNCERTAIN` (info); never a warning |
Tone findings never block or trigger repair.

## 5. Repair
Repair edits only the target text, only for these findings, and only when all preconditions hold.
| Finding | Edit | Preconditions |
|---|---|---|
| `NUMBER_CHANGED` (MONEY, COUNT, PERCENT) | replace the target's number span with the source's value in the target's own notation (Indian grouping, lakh/crore, Devanagari digits if the target uses them) | exactly one source fact and one target fact of that kind and role; source tier 1; target tier 1 or 2; no number flags |
| `CURRENCY_CHANGED` / `CURRENCY_MISSING` | replace or insert the currency token | same as above |
| `NEGATION_DROPPED` | none (reported only) | negation insertion needs verb morphology and is out of scope |
| everything else | none | |

After an edit the pipeline re-parses the repaired text and re-verifies:
1. The targeted finding must be gone.
2. No new `CHANGED` or `MISSING` finding may appear.
3. Every fact that matched before still matches.
4. Otherwise the edit is discarded and the finding reported unrepaired (`REPAIR_REVERTED`).
Repair is idempotent: running it on its own output makes no further edit. At most one repair pass
runs. Every repair is shown in the UI with before and after text.

## 6. Spec case file format (`tests/spec_cases/*.jsonl`, one JSON object per line)
| Field | Meaning |
|---|---|
| `id` | unique, `<area>-<nnn>` |
| `area` | `lexer`, `numbers`, `negation`, `parser`, `semantics`, `verifier`, `repair` |
| `lang` | `en`, `hi`, or `pair` |
| `input` | the text (for `pair`: `{"source": ..., "target": ...}`) |
| `expect` | what must hold (area-specific, see the file header) |
| `must` | `pass` (must parse or verify as expected) or `fail` (must be rejected or flagged) |
| `rule` | the spec rule it tests, e.g. `NUM-2`, `C-UNLESS` |
| `notes` | optional |
