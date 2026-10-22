# 07 Parser (v1)

How `meaningguard/parser.py`, `semantics.py` and `canon.py` turn tokens into the IR of 05. The grammars
in 03 and 04 give the surface forms; this file gives the stages, the rules the grammars leave
open, and the decisions made on the dev corpus.

## 1. Stages
| Stage | Module | Output |
|---|---|---|
| Lex, assemble numbers | `lexer`, `numerics` | tokens, quantities, times |
| Elements | `parser.build_elements` | value, relation, negation, word and punctuation elements |
| Merges | `merge_dates`, `merge_durations`, `merge_dayparts` | dates, durations, AM/PM from a part of the day |
| Segmentation | `segment` | clauses with an optional conditional marker |
| Island parse | `ClauseParser` | raw facts per clause (panic mode) |
| Semantic pass | `semantics.analyze` | C-CUR-RANGE, C-DIR, polarity, conditional links |
| Canonicalization | `canon.canonicalize` | C-UNLESS, C-TIMEREL, C-BOUND-NEG, C-RANGE, C-ORDER |
| Fingerprints, IR | `fingerprint`, `ir` | `mgir/1` |

## 2. Element merges
| Id | Rule |
|---|---|
| E-DEC | Two numbers joined by one glued comma (`1,5`, `१,५`), both flagged `COMMA_DECIMAL_SUSPECT`, are one decimal (1.5), one clause; the flag stays on the value. |
| E-DATE | `N MONTH`, `MONTH N`, `MONTH N, YEAR`, `N MONTH YEAR`, `MONTH YEAR` (the day may be an ordinal: `3rd March`, `March 3rd`, `3rd of March`) become one DATE (day 1 to 31, year 1900 to 2100). A day number after the month is taken only if the next word is a function word (`December 31 is`), so `In June 20 people came` keeps 20 as a quantity. Numeric dates (`15/10/2026`, `2026-10-15`) are day first unless a part is above 12 (flag `DATE_ORDER_ASSUMED`). |
| E-DUR | A COUNT directly followed by a unit word of `lexicons/durations.json` (one or two words, an optional glued hyphen: `7-day`) is a DURATION. |
| E-DAYPART | A part of the day directly before or after a TIME with no period gives it AM or PM (`शाम 5 बजे` = 5 PM, `रात 1 बजे` = 1 AM; flag `PERIOD_FROM_DAYPART`); directly after a weekday it becomes the DATE `daypart`. A bare part of the day states no fact. |

## 3. Segmentation
A clause ends at `,` `;` a sentence end, or a clause word (`but while so yet`; `लेकिन मगर जबकि इसलिए`).
`and` and `और` end a clause unless they join the two ends of `between` / `के बीच`. A conditional word
opens a clause and is removed: `if`, `unless`, `otherwise`, `अगर`, `जब तक`, `वरना`. `then`/`तो` closes an open
antecedent and is dropped; with no open antecedent it is an ordinary word. `जो` is a conditional only
when a `तो` follows in the same sentence. `पर` and `को` are time relations only directly after an anchor.

## 4. Constructs
A construct is recognised by its first element and consumes its operands. Up to two unit words may sit
between a value and the next relation word (`300 units to 450 units`) at no cost.

| Fact | English | Hindi |
|---|---|---|
| CHANGE | `[rose] from A to B`, `to B`, `by A`, `rose A`, `A rise`, `rise of A`, `to B from A`, bare past verb | `A से PART B`, `PART B`, `A से B तक VERB`, `A VERB`, `A की NOUN [हुई]`, bare verb |
| RANGE | `between A and B`, `from A to B`, `A to B`, `A-B` | `A से B [तक]`, `A और B के बीच`, `A-B` |
| BOUND | `more than A`, `A or more` | `A से ज़्यादा`, `कम से कम A`, `A या ज़्यादा`, `A तक` (up to, flag `TAK_AS_UPTO`) |
| TIMEREL | `before/after/by/until/on/within/since ANCHOR` | `ANCHOR के बाद/से पहले/तक/को/में/के भीतर` |
| NEG | adjacent to a relation word: scope REL; otherwise CLAUSE; `without X`: scope QUANT | after a relation phrase ending the clause (optionally before a copula `है`): scope REL; otherwise CLAUSE |

**Bare change.** A finite past verb with no operand states a direction (`the fee increased`): CHANGE with no
values. Base forms and `up`/`down` need an operand, so `drop the form` and `sit down` create nothing.
**A change verb before a bound** (`falls below 400`) creates only the BOUND.
**`से बढ़कर`.** Spec 04: a bound when no value follows, a change (RISE from A to B) when a value follows;
the other reading is recorded as the info diagnostic `AMBIGUOUS_PARTICIPLE`, not as a second parse.
**Lone `से`** after a number: `QUANT(role=AMBIGUOUS)` and the warning `AMBIGUOUS_FROM`.
**`or` / `या`** between facts: the facts are kept as separate facts and `UNSUPPORTED_STRUCTURE` is raised.

## 5. Tiers
Tier 1: every relation word was consumed and nothing was skipped inside a construct. Tier 2: a relation word
has no operand (`UNATTACHED_REL`; the particles `to from on by within से तक को में पर` are exempt), words were
skipped inside a construct (`SKIPPED_IN_ISLAND`), `UNSUPPORTED_STRUCTURE`, `AMBIGUOUS_FROM`, an unfinished
conditional, or a number error. Tier 3: nothing was recognised (no facts, no conditional, no negation), or
every clause fell back to lexer-only facts after `INTERNAL_PARSE_ERROR`.

## 6. Conditionals
`IF`/`UNLESS` opens an antecedent. Its consequent is the next clause when the antecedent is first in the
sentence, otherwise the previous clause. `ELSE` points at the previous clause. `जब तक A नहीं` consumes the
negation and becomes `UNLESS` (`UNTIL_AS_UNLESS`); `जब तक A` without a negation becomes `IF` (`UNTIL_AS_IF`).
Conditionals never cross a sentence end (`MULTI_SENTENCE` is raised when the input has several sentences).

## 7. Known limits
- A change fact whose subject comes in another clause (`From A to B, the price fell`, with a comma) is a RANGE.
- `or` alternatives are not modelled.
- Subjects, objects and predicates are opaque text; only quantities, relations, time, negation and
  conditionals are checked.
- Scale and AM/PM sharing apply inside one range only.

## 8. Diagnostic codes
Every code is defined in `meaningguard/diagnostics.py` (severity and default message) and listed here;
`tests/test_ir.py` checks that the two agree.

| Stage | Codes |
|---|---|
| Lexer flags (01 section 5) | `MIXED_DIGIT_SCRIPT`, `COMMA_DECIMAL_SUSPECT`, `DOT_THOUSANDS_SUSPECT`, `AMBIGUOUS_ABBREV`, `NON_QUANTITY`, `NEGATIVE`, `INFORMAL_NEG`, `AMBIGUOUS_CURRENCY`, `CURRENCY_CONFLICT`, `WEAK_ONE`, `AMBIGUOUS_NUMWORD`, `FRACTION_WORD`, `BARE_SCALE`, `MIXED_SCRIPT`, `UNTRANSLATED_LATIN`, `SCALE_ORDER` |
| Parser | `UNSUPPORTED_STRUCTURE`, `UNATTACHED_REL`, `SKIPPED_IN_ISLAND`, `AMBIGUOUS_FROM`, `AMBIGUOUS_PARTICIPLE`, `UNASSEMBLED_NUMBER`, `COND_INCOMPLETE`, `MULTI_SENTENCE`, `INTERNAL_PARSE_ERROR`, `TAK_AS_UPTO`, `DATE_ORDER_ASSUMED`, `PERIOD_FROM_DAYPART`, `APPROX_IN_COMPOUND`, `IMPLICIT_ON`, `WEAK_ONE_DROPPED` |
| Semantics and canonicalization | `SOURCE_INCONSISTENT`, `CHANGE_NO_DELTA`, `RANGE_REVERSED`, `CURRENCY_INHERITED`, `PERCENT_INHERITED`, `UNIT_INHERITED`, `SCALE_SHARED`, `PERIOD_SHARED`, `CURRENCY_MISMATCH_IN_RANGE`, `TIME_PERIOD_MISSING`, `BY_UNTIL_AMBIGUOUS`, `DOUBLE_NEG`, `UNTIL_AS_UNLESS`, `UNTIL_AS_IF`, `UNLESS_REWRITTEN`, `NEG_REWRITTEN` |
