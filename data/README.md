# Data (Phase 2)

| File | What it is |
|---|---|
| `sentences.jsonl` | 194 English source sentences in 20 categories, with gold facts and a dev/held-out split |
| `corruptions.jsonl` | 29 corrupted Hindi translations plus 6 correct controls, each with the finding the verifier must report |
| `translations.jsonl` | IndicTrans2 output for the sentences (created by `tools/translate_dataset.py`, on your machine) |
| `review/*.csv` | sheets for the Hindi reviewer and the emotion annotators (`python tools/make_review_sheets.py`) |

## Split rule
Inside each category, the sentence at position 2, 5, 8, ... (0-based, `index % 3 == 2`) is held out: 61 of 194.
Tune and debug on **dev only**. Run held-out once, at evaluation time (Phase 9). The lexer was written before
this data existed; the parser (Phase 3b) is built against dev.

## Gold fields (`sentences.jsonl`)
`gold.quantities` (value, type, currency), `gold.rels` (`CHANGE:RISE`, `RANGE`, `BOUND:GTE`, `TIMEREL:AFTER`,
`NEG`, `NEG:QUANT`, `DOUBLE_NEG`, `COND:IF`, ...), `gold.times`, `gold.dates`, `gold.vague`.
Date parts (the 12 in `12 November`) are in `gold.dates`, not `gold.quantities`.
`emotion` uses the 7 labels of the emotion model; `sarcasm` is true/false. `annotation: proposed` means the
label is my proposal and still needs two human labels (you and a second annotator) before it counts as gold.

## Gold rules (decided in Phase 3b)
- Durations (`within 7 days`) are `quantities` of type `DURATION` with a `unit`; `1 week` and `7 days` are different units.
- `rels` ignores `TIMEREL:ON`: the gold lists it for `on Monday` but not for `at 6 PM`, so tests leave it out.
- Gold was corrected on dev only (s037, s080, s091, s094, s151, s153, s163, s168, s169, s177, s187). Held-out was not read.

## Open questions
- Hindi in `corruptions.jsonl` is written by the author. All 35 pairs were checked by a Hindi reader (`review/hindi_review_filled.csv`): every reference was confirmed and no correction was needed, so they are marked `hindi_review: confirmed`.

## Steps on your machine
1. `python tools/translate_dataset.py --split dev` (add `--back` later for the emotion phase), then `--split all` before Phase 9.
2. `python tools/make_review_sheets.py`, send `data/review/hindi_review.csv` to the reviewer, and fill your own copy of `emotion_labels.csv`.
