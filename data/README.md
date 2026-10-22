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

## Open questions
- Durations (`within 7 days`, `after 30 days`) have no anchor type in the spec yet. The 3 sentences that use
  them are in `timerel` and `combined`. Phase 3b will decide (likely `TIMEREL` with a COUNT duration).
- Hindi in `corruptions.jsonl` is written by me and marked `hindi_review: pending` until a native speaker confirms it.

## Steps on your machine
1. `python tools/translate_dataset.py --split dev` (add `--back` later for the emotion phase), then `--split all` before Phase 9.
2. `python tools/make_review_sheets.py`, send `data/review/hindi_review.csv` to the reviewer, and fill your own copy of `emotion_labels.csv`.
