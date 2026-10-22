# 01 Lexical specification (v1)

Status: implemented in `meaningguard/lexer.py`, `lexicon.py` and `numerics.py`. A native Hindi speaker has reviewed the Hindi parts.

## 1. Input contract
- One sentence per call, 1 to 1000 characters. Empty or whitespace-only input raises `EmptyInput`;
  longer input raises `InputTooLong`.
- Multi-sentence text is split first. `.` ends a sentence only if followed by whitespace and an
  uppercase Latin letter, a Devanagari letter, a digit or a currency symbol, or by end of text, and the
  word before it is not in the abbreviation list `{rs, no, a.m., p.m., mr, mrs, dr, st, approx, vs,
  etc, inr, ms, prof, jr, sr}` or a single capital initial (`A.`). `no` is an abbreviation only
  before a digit (`No. 5`), so `I said no. Then left.` is two sentences. Closing quotes and
  brackets after a sentence end stay in that sentence. A run of `?!।॥` is one `SENT_END`.
  `?`, `!`, `।` (U+0964) and `॥` (U+0965) always end a sentence.

## 2. Normalization (applied in this order; offsets map back to the original text)
| Id | Rule |
|---|---|
| N1 | Unicode NFC. (NFC turns precomposed nukta letters such as U+095B into base + U+093C.) |
| N2 | Delete U+200B, U+200C, U+200D, U+FEFF, U+00AD. |
| N3 | Quotes ‘ ’ ʼ become `'`; “ ” become `"`; en dash, em dash and U+2212 become `-`. |
| N4 | Every Unicode space (Zs, including NBSP) becomes one space; runs collapse to one. |
| N5 | Lexicon match key for Devanagari: remove U+093C (nukta), map U+0901 (chandrabindu) to U+0902 (anusvara). `हज़ार` and `हजार` share a key; `नहीं` and `नही` do not (both are listed). |
| N6 | Latin matching is case-insensitive (`casefold`); display text keeps the original case. |
| N7 | Digits: any Unicode `Nd` character is a digit. Value uses `unicodedata.digit`. |
| N8 | Latin-script Hindi (`nahi`, `rupaye`) is not supported; those words lex as `WORD`. |

## 3. Token types
| Type | Matches | Notes |
|---|---|---|
| `NUMBER` | digit sequence per section 4 | carries `value`, `flags` |
| `NUMWORD` | English or Hindi number word, fraction word | `numbers_en.json`, `numbers_hi.json` |
| `SCALE` | thousand, lakh, crore, million, billion, ... | `scales.json`; attached abbreviations only directly after a digit |
| `VAGUE` | hundreds, thousands, lakhs of, लाखों, कई, कुछ | no numeric value; flagged |
| `CURRENCY` | symbol, code, word, or `/-` | `currency.json`; carries ISO code |
| `PERCENT` | `%`, percent, per cent, प्रतिशत, फ़ीसदी | |
| `NEG` | negators | `negation.json`; carries `kind` |
| `REL` | comparison, range, condition and time-relation words | `relations.json`; carries `role` |
| `DAY`, `MONTH` | weekday and month names | `calendar.json` |
| `TIMEMARK` | AM, PM, a.m., p.m., o'clock, बजे | |
| `TIME` | clock literal `H:MM` or `H:MM:SS` (hour at most 24, minute at most 59) | carries `hour`, `minute` |
| `DAYPART` | morning, noon, evening, night, सुबह, दोपहर, शाम, रात | |
| `WORD` | other letters (Latin or Devanagari, plus internal `'` and `-`) | |
| `PUNCT` | any other single non-space character except the sentence enders | |
| `SENT_END` | `. ? ! । ॥` per section 1 | not part of any word |

Longest match wins at each position. Multi-word lexicon entries (`से ज़्यादा`, `no longer`,
`at least`) are matched before single words and produce one token.

Word characters are: Latin letters, Devanagari letters and combining marks U+0900 to U+0963, and
U+0971 to U+097F. U+0964 and U+0965 (dandas) are never word characters. This fixes a defect in the
prototype lexer, where the danda was inside the word range and could hide a negation at sentence end.

## 4. Number lexing
| Id | Rule |
|---|---|
| NUM-1 | Plain digits `\d+`, any mix of scripts. A mix of scripts inside one number sets flag `MIXED_DIGIT_SCRIPT`. |
| NUM-2 | Western grouping: `\d{1,3}(,\d{3})+`. Indian grouping: `\d{1,2}(,\d{2})*,\d{3}`. Both are accepted; the value ignores commas. A comma followed by one or two digits that fit neither pattern is `PUNCT`, not a separator (`1,5` is `NUMBER PUNCT NUMBER`, flag `COMMA_DECIMAL_SUSPECT` on both). |
| NUM-3 | Decimal: `\d+\.\d+`. A `.` not followed by a digit is not part of the number (`₹400.` is `NUMBER SENT_END`). |
| NUM-4 | `\d{1,3}\.\d{3}` is read as a decimal (`1.500` is 1.5) and flagged `DOT_THOUSANDS_SUSPECT`. |
| NUM-5 | Attached scale abbreviation: `5k`, `5L`, `2M`, `3cr`, `2bn` (no space). Single letters `l`, `m`, `b` set flag `AMBIGUOUS_ABBREV` (metres, litres). Spaced single letters are plain `WORD`. |
| NUM-6 | Stacked scales: `2 crore 50 lakh` is allowed; scales must strictly decrease (crore, lakh, thousand, hundred). An increase (`5 lakh 2 crore`) is a parse error `SCALE_ORDER`. |
| NUM-7 | Number words: English compounds `twenty-five`, `one hundred and five`; Hindi words from `numbers_hi.json` (0 to 100 plus fraction words). |
| NUM-8 | Hindi fraction words: `सवा` (+0.25), `डेढ़` (1.5), `ढाई` (2.5), `साढ़े` (+0.5), `पौने` (-0.25), `आधा` (0.5). Rules in `02_types_semantics.md` section 4. |
| NUM-9 | Range: two numbers joined by `-`, `to`, `से ... तक` is a range construct handled by the parser, not the lexer. |
| NUM-10 | Not quantities (stay `WORD`/`PUNCT`, flag `NON_QUANTITY`): `No. 5`, `Rs.` without a number, phone numbers (10 or more digits with no separators, or `+\d{1,3}[ -]\d{5,}`), version numbers `\d+\.\d+\.\d+`, dates `\d{1,2}[/-]\d{1,2}[/-]\d{2,4}`, ordinals (`1st`, `2nd`, `3rd`, `4th`, `पहला`). |
| NUM-12 | Identifiers (Latin letters glued to digits: `A5`, `H2O`, `MP3`, `COVID-19`) are one `WORD` with flag `NON_QUANTITY`. Currency codes and month/day words are exempt (`Rs5`, `USD500`, `Jan5`). Digits followed by letters stay a number plus a word (`5km` is `NUMBER WORD`). |
| NUM-13 | A `-` directly before a digit, with no word character, digit, `-`, `/` or `.` before it, is a minus sign: `-5` is the number -5 (flag `NEGATIVE`). `5-10` and `₹500-₹600` are not negative. |
| NUM-14 | A leading decimal `.5` is 0.5 when no word character or digit precedes the dot. |
| NUM-15 | Phone numbers, numeric dates, versions, ordinals, `No. 5` and identifiers are opaque `WORD` tokens (`attrs.opaque`, `attrs.digits` in ASCII digits). The verifier may compare them as exact strings. A bare run of 10 or more digits is phone-like; a grouped number (`1,00,00,00,000`) is not. |
| NUM-11 | Time: `\d{1,2}:\d{2}` and `\d{1,2}\s?(AM|PM|a.m.|p.m.)` and `N बजे` are `TIME` literals, not counts. |

### 4.1 Number assembly (numerics.py)
| Id | Rule |
|---|---|
| ASM-1 | Digits, number words and scales are evaluated left to right as `Decimal`. Hindi words below 100 are atomic (`अट्ठाईस`); English `twenty-five` joins tens and units, with `and` allowed after a scale (`five hundred and five`). `and` is a range separator, not part of a number, after `between`. |
| ASM-2 | A digit group after a scale needs its own smaller scale: `5 lakh 20 thousand` is 520000, but `5 lakh 20` is two quantities. Number words after a scale add (`पाँच हज़ार दो सौ` is 5200). Words that do not combine (`five 10`, `दो-तीन`) are separate quantities. |
| ASM-3 | `a`/`an` before a scale means one (`a hundred`). `dozen`/`दर्जन` multiply by 12 and are exempt from scale order. A scale word with no number (`lakh`) is 1 times the scale with flag `BARE_SCALE`. |
| ASM-4 | Fractions: `सवा`, `साढ़े`, `पौने`, `डेढ़`, `ढाई`, `आधा` as in `numbers_hi.json`; `पौने` with no number is 0.75; `साढ़े` with no number is not a quantity. English `half a million`, `a quarter of a crore`, `N and a half`, and `½ ¼ ¾` (`1½ lakh`). A fraction below 1 with no scale (`half`, `आधा`, `a quarter`, `three quarters`) is a quantity with flag `FRACTION_WORD`; a bare `quarter` ("this quarter") is a period, not a quantity. |
| ASM-5 | Times from words: `N बजे`, `N o'clock`, `N AM/PM` (1 to 12 for AM/PM, 0 to 24 otherwise); Hindi `सवा N बजे` = N:15, `साढ़े N बजे` = N:30, `पौने N बजे` = (N-1):45, `डेढ़ बजे` = 1:30, `ढाई बजे` = 2:30. Out-of-range values stay quantities. |
| ASM-6 | A number followed by a percent token is `PERCENT`. Otherwise `COUNT`, or `MONEY` once a currency attaches. |
| ASM-7 | Currency attachment, by token style: symbols (`₹ $ € £ ¥`) attach to the number after them; `/-` attaches to the number before; codes (`Rs`, `INR`, `USD`, `रु.`) prefer the number after, then before; words (`rupees`, `रुपये`, `dollars`) prefer the number before, then after. Each number takes at most one currency; a second, different currency next to a claimed number sets `CURRENCY_CONFLICT`. `pound(s)` followed by `of` is a weight, not money. In `5 से 10 रुपये` only the second number has a currency; the parser applies it to the range (rule C-CUR-RANGE). |
| ASM-8 | `one` is not a quantity when it is a pronoun (`one of`, `the one`, `which one`, `no one`). `एक`/`one` alone is a quantity with flag `WEAK_ONE`, which the verifier treats as optional (it can be an article). `दो` at a clause end may be the verb "give": flag `AMBIGUOUS_NUMWORD`. |
| ASM-9 | Spaced scale words `cr`, `mn`, `bn`, `अरब` are scales only after a number; otherwise plain words. |

## 5. Flags
`MIXED_DIGIT_SCRIPT`, `COMMA_DECIMAL_SUSPECT`, `DOT_THOUSANDS_SUSPECT`, `AMBIGUOUS_ABBREV`,
`NON_QUANTITY`, `NEGATIVE`, `INFORMAL_NEG`, `AMBIGUOUS_CURRENCY`, `CURRENCY_CONFLICT`, `WEAK_ONE`, `AMBIGUOUS_NUMWORD`, `FRACTION_WORD`, `BARE_SCALE`, `MIXED_SCRIPT` (sentence has Latin and Devanagari words),
`UNTRANSLATED_LATIN` (Hindi output contains Latin words of 3 or more letters that are not
brand names in the lexicon). Flags never change a value; they lower confidence and are shown in the UI.

## 6. Lexicon files (all under `meaningguard/lexicons/`)
`negation.json`, `currency.json`, `scales.json`, `relations.json`, `calendar.json`,
`numbers_en.json`, `numbers_hi.json`. Entries are stored in display form and matched by key
(N5, N6). Two different canonical values must never share a key (enforced by
`tests/test_lexicons.py`).
