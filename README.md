# MeaningGuard-Indic

A compiler-style verifier for English to Hindi translation. It lexes and parses both sentences into a
typed AST, canonicalizes them, compiles each to an IR (`mgir/1`) and compares the facts: numbers,
currency, negation, direction, comparison, time and dates. Wrong numbers and currencies are repaired
with a minimal edit; every other error is reported with a suggested fix. An optional tone check
compares emotion and flags possible sarcasm. Pretrained models are used only to translate
(IndicTrans2) and to score emotion; parsing, IR, verification and repair are deterministic.

## Setup

    git clone https://github.com/albymariam/meaningguard.git
    cd meaningguard
    python3.11 -m venv .venv && source .venv/bin/activate     # Python 3.10 to 3.13 works
    pip install -r requirements-dev.txt

On Windows use WSL2 and keep the repo in the Linux home folder, not under `/mnt/c`.

The IndicTrans2 models are gated: accept the terms on `ai4bharat/indictrans2-en-indic-dist-200M`
and `ai4bharat/indictrans2-indic-en-dist-200M`, create a read token, and `export HF_TOKEN=hf_...`.
Never commit it. `python -m meaningguard.check_models --full` checks the environment and models.

## Use

    from meaningguard.pipeline import build_pipeline
    result = build_pipeline().check("Pay ₹5 lakh by Friday.")            # translate, verify, repair
    result.final, result.report.status, result.report.codes()
    build_pipeline().check("Pay ₹5 lakh.", "५० लाख रुपये दें।")          # check a translation you have

IndicTrans2 is tried first, then the Google fallback (`runtime.google_fallback` in `config.yaml`).
`build_pipeline(tone=True)` adds the emotion and sarcasm check (`docs/spec/08_emotion.md`).
Model outputs are cached in `.cache/meaningguard.sqlite` (`python -m meaningguard.cache stats|clear`).

Web app: `python -m streamlit run app.py`.

## Tests

    pytest -q && ruff check .

## Evaluation

Results are in [`reports/README.md`](reports/README.md). To reproduce them, open
`notebooks/phase9_colab.ipynb` on a Colab GPU (it needs the Hugging Face token as a Colab secret), or run:

    python tools/translate_dataset.py --split all --back
    python tools/eval_verifier.py --mt data/translations.jsonl
    python tools/eval_emotion.py --target \
      --labels data/review/emotion_labels.csv data/review/emotion_labels_2.csv \
      --labels2 data/review/emotion_labels_annotator2.csv data/review/emotion_labels_2_annotator2.csv

Data (`data/`): 259 sentences with gold facts, 35 hand-written corrupted translations (Hindi checked by a
reviewer), and emotion labels from two annotators. Thresholds were tuned on the dev split only. The
held-out split was used once, and its tools refuse to run without `--i-know-this-spends-heldout`.

## Layout

    meaningguard/   lexer, parser, canon, ir, verifier, repair, hints, emotion, tone, pipeline, cache
    docs/spec/      the specification, 01 to 08
    tools/          evaluation and data tools      data/, reports/   datasets and results
    app.py          Streamlit demo                 tests/            unit and spec tests
