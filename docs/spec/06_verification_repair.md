# 06 Verification and repair (v1)

## 1. Alignment
Only clauses that say something count: a clause with no fact, no negation and no condition is
ignored (it is also left out of the sentence fingerprint). Clauses are paired by equal fingerprint,
then by conditional role, then by order, so a reordered conditional still aligns. A clause whose
facts were all lost pairs with an empty clause of the other side. If clauses are still left over,
the verifier reports `CLAUSE_COUNT_CHANGED` (warning) and compares the facts of the whole sentence.

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
`AMBIGUOUS_NUMBER` (warning), `UNVERIFIED` (info), `REPAIR_REVERTED`, `LITERAL_CHANGED` (a phone
number, version or identifier differs), `CONDITION_CHANGED` (warning; the conditional role of a clause
differs), `SOURCE_INCONSISTENT` (warning; the source verb contradicts its own numbers, the numbers win).
A `CHANGE` whose target verb contradicts the target numbers (flag `SOURCE_INCONSISTENT` on the target)
while the source is consistent is `DIRECTION_CHANGED`: the fingerprint holds the direction computed from
the numbers, so the verb (`verb` field) is compared as well.
Each finding keeps every difference of its fact pair in `diffs` (path, code, source value, target value),
so repair can act on one field (`to` of a `CHANGE`, `high` of a `RANGE`). A finding is `unique` when
its pair was the only leftover fact of its kind on both sides.
When one source fact differs from one target fact in several ways, the most specific code wins in
this order: `DIRECTION_CHANGED`, `CURRENCY_*`, `COMPARISON_CHANGED`, `NUMBER_CHANGED`.

## 3. Confidence and tier
- Tier 1 on both sides: findings are reported as is.
- Tier 2 on either side: findings are reported, and `MISSING` becomes `UNVERIFIED` (info) because the
  parser may have skipped the fact.
- Tier 3 on either side: only number, currency and clause-polarity checks run (every numeric value of
  every fact is compared); every result carries `confidence: low` and the UI label "basic check". A
  source number missing from the translation is then `FACT_MISSING` as a warning, not an error, because the
  translation may hold it in a form the parser cannot read.
- Equal values written with different ambiguous notations (`1.500` and `१,५`) give `AMBIGUOUS_NUMBER`.
- Lexer flags `COMMA_DECIMAL_SUSPECT`, `DOT_THOUSANDS_SUSPECT`, `MIXED_DIGIT_SCRIPT` on a number
  downgrade its `CHANGED` to `AMBIGUOUS_NUMBER` (warning) and block repair for that fact.

## 4. Tone comparison (uses the Emotion record)
Run on the final text, after repair. First matching row wins; a source with `possible_sarcasm` only
reaches the second row, because its surface sentiment is not its meaning.
| Source | Target | Result |
|---|---|---|
| `possible_sarcasm` = true | sentiment `positive` | `SARCASM_POSSIBLY_LOST` (warning) |
| `positive` | `negative` (or reverse) | `TONE_POLARITY_FLIPPED` (warning) |
| `neutral` | `positive`/`negative`, confidence >= 0.7 | `TONE_INTENSIFIED` (info) |
| `positive`/`negative` | `neutral`, confidence >= 0.7 | `TONE_WEAKENED` (info) |
| `positive`/`negative` | `uncertain` | `TONE_UNCERTAIN` (info) |
| same sentiment | two different labels, neither `neutral`/`uncertain` | `EMOTION_CHANGED` (info) |
Tone findings have `confidence: low`, no source or target fact, never count as `fail`, and never
trigger repair. If either record is missing (models unavailable) there are no tone findings.

## 5. Repair
Repair edits only the target text, only for these findings, and only when all preconditions hold.
| Finding | Edit | Preconditions |
|---|---|---|
| `NUMBER_CHANGED` (MONEY, COUNT, PERCENT) | replace the target's number span with the source's value in the target's own notation (Indian grouping, lakh/crore, Devanagari digits if the target uses them) | the finding is `unique` (one leftover source fact and one leftover target fact of that kind and role); source tier 1; target tier 1 or 2; no number flags |
| `NUMBER_CHANGED` (DURATION amount, same unit) | same, the unit word is kept | same as above |
| `CURRENCY_CHANGED` / `CURRENCY_MISSING` | replace or insert the currency token (symbol if the target uses symbols, else the Hindi word) | same as above |
| `NEGATION_DROPPED` | none (reported only) | negation insertion needs verb morphology and is out of scope |
| everything else | none | |

The number is written with the target's own digit script, scale word (a scale that no longer fits is
replaced by the largest one that gives at most two decimals) and grouping (Indian for INR, otherwise
copied from the target). The edits of one finding form one group; groups are applied from the end of the
text backwards. After an edit group the pipeline re-parses the repaired text and re-verifies:
1. The targeted finding must be gone.
2. No new finding of severity warning or error may appear.
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
