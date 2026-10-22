# 02 Types and semantic rules (v1)

## 1. Value types
All numbers are `decimal.Decimal` (never `float`), so `सवा लाख` is exactly `125000`.

| Type | Fields | Example |
|---|---|---|
| `MONEY` | `amount`, `currency` (ISO code) | `₹५ लाख` = MONEY(500000, INR) |
| `COUNT` | `amount` | `500 people` = COUNT(500) |
| `PERCENT` | `amount` | `12.5%` = PERCENT(12.5) |
| `TIME` | `hour`, `minute`, `period` (AM, PM, or none) | `10 AM`, `५ बजे` |
| `DATE` | `weekday`, `day`, `month`, `year` (each may be null) | `Friday`, `15 October` |
| `VAGUE` | `order` (power of ten) | `लाखों` = VAGUE(5) |

A bare number with no currency, percent or time marker is `COUNT`. A number with a currency token
is always `MONEY`. The currency is never guessed.

## 2. Compound facts
| Node | Fields | Built from |
|---|---|---|
| `QUANT` | value, role, approx | one value, optionally marked approximate |
| `CHANGE` | direction (`RISE`, `FALL`), from, to, delta | RISE/FALL verb with from/to/by operands |
| `RANGE` | low, high | `between A and B`, `A to B`, `A-B`, `A से B तक` without RISE/FALL |
| `BOUND` | op (`GT`, `GTE`, `LT`, `LTE`), value | comparison words |
| `TIMEREL` | rel (`BEFORE`, `AFTER`, `ON_OR_BEFORE`, `ON_OR_AFTER`, `ON`, `WITHIN`), anchor (TIME or DATE) | time-relation words |
| `NEG` | scope (`CLAUSE`, `REL`, `QUANT`), target | negators |
| `COND` | op (`IF`), antecedent, consequent, else | conditionals |

## 3. Negation scope and polarity
- A clause has polarity `POS` or `NEG`. A negator makes the clause `NEG` unless one of the rules
  below applies. Two negators in one clause give polarity `POS` and flag `DOUBLE_NEG` (never
  repaired).
- **Adjacency rule (relation scope).** A negator directly adjacent to a relation phrase and not
  separated from it by another word, in the order the language uses, scopes over that phrase only.
  English: `NEG` immediately before a relation word (`submit not after Friday`). Hindi: `NEG`
  immediately after a relation phrase that ends the clause (`शुक्रवार के बाद नहीं`).
- **Clause scope.** Otherwise the negator scopes over the clause predicate (`do not submit after
  Friday`, `शुक्रवार के बाद जमा न करें`).
- **Quantifier scope.** `NEG_PRON` (`none`, `nothing`, `no one`, `कुछ नहीं`) makes the clause
  polarity `NEG` and creates `NEG(scope=CLAUSE)`. `NEG_PREP` (`without`, `बिना`) leaves the clause
  polarity unchanged and creates `NEG(scope=QUANT)` on the phrase it heads.
- **Correlatives.** `neither ... nor` and `न ... न` count as one negation.
- **Non-negating uses** listed in `negation.json` (`नहीं तो`, tag-question `है न?`, `not only`,
  `No. 5`) never create `NEG`.
- **Weak negators** (`hardly`, `शायद ही`) create `NEG(strength=WEAK)`; the verifier accepts any
  negator on the other side but reports `NEG_STRENGTH_CHANGED` at info level.

## 4. Number-word and fraction evaluation
Rules from `numbers_hi.json` apply: `सवा N M = (N + 0.25) x M`, `साढ़े N M = (N + 0.5) x M`,
`पौने N M = (N - 0.25) x M`, `डेढ़ M = 1.5 x M`, `ढाई M = 2.5 x M`. English: `half a million =
500000`, `a quarter of a crore = 2500000`. Evaluation is left to right; a number word run that
violates the scale order (NUM-6) is a `SCALE_ORDER` error and yields no value.

## 5. Canonicalization (C-rules, applied in order)
| Id | Rule |
|---|---|
| C-NUM | Fold every number expression to one Decimal (constant folding). `5 lakh` = `500000` = `पाँच लाख` = `5,00,000`. |
| C-CUR | Map every currency token to its ISO code. |
| C-DIR | `CHANGE.direction` is computed from the sign of `to - from` when both exist. The verb is only a consistency check: a mismatch (`rose from 500 to 400`) flags `SOURCE_INCONSISTENT` and is never repaired. |
| C-UNLESS | `COND(UNLESS, A, B)` becomes `COND(IF, NEG(A), B)`. Hindi `जब तक A नहीं, B` becomes the same form, with info diagnostic `UNTIL_AS_UNLESS` (the Hindi construction is closer to "until"; the approximation is recorded, not hidden). |
| C-TIMEREL | `BY` and `UNTIL` and Hindi `तक` become `ON_OR_BEFORE` (flag `BY_UNTIL_AMBIGUOUS` at info level). `NEG(AFTER x)` becomes `ON_OR_BEFORE x`; `NEG(BEFORE x)` becomes `ON_OR_AFTER x`. |
| C-BOUND | `at least` becomes `GTE`, `no less than` becomes `GTE`, `at most` and `up to` become `LTE`, `not more than` becomes `LTE`. |
| C-RANGE | `A to B` with no RISE/FALL verb and `A > B` flags `RANGE_REVERSED`; low and high are swapped in the canonical node only if the source also lists them as `low high` explicitly. |
| C-ORDER | Sibling facts in a clause are sorted by node type then by canonical JSON so reordering does not change fingerprints. |
| C-SLOT | Each QUANT receives a `role` from its parent construct (`FROM`, `TO`, `LOW`, `HIGH`, `LIMIT`, `AMOUNT`, `DEADLINE`). Surface words and positions are dropped (slot canonicalization). |

## 6. Fingerprints
`fingerprint(node) = first 16 hex chars of SHA-256 over canonical JSON of
(node type, canonical attributes, child fingerprints in canonical order)`. Surface text, token
positions and flags are excluded. Two nodes are equal if and only if their fingerprints match.
Decimal values are serialized with `normalize()` and a fixed format, so `500000`, `5E+5` and
`500000.00` give the same string.

## 7. Equality and tolerance
- `MONEY`, `COUNT`, `PERCENT`: exact Decimal equality. There is no numeric tolerance.
- `QUANT.approx` on one side only: reported as `PRECISION_CHANGED` (warning), not a mismatch.
- `TIME`: equality after 12-hour to 24-hour normalization; `10 AM` = `10:00` = `१० बजे` only if
  the period is known on both sides. Missing period on one side: `TIME_PERIOD_MISSING` (warning).
- `DATE`: equal if every field present on both sides matches; a field present only on one side is
  `DATE_PRECISION_CHANGED` (warning).
- `VAGUE`: target order must be at least the source order.
