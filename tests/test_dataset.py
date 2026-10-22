"""Validates the Phase 2 data files and cross-checks them against the lexer."""

import json
import re
import unicodedata
from pathlib import Path

import pytest

from meaningguard.lexer import lex
from meaningguard.numerics import analyze

ROOT = Path(__file__).resolve().parent.parent
SPEC = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "docs" / "spec").iterdir()))
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
EMOTIONS = {"anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise"}
REL = re.compile(
    r"^(CHANGE:(RISE|FALL)|RANGE|BOUND:(GT|GTE|LT|LTE)|NEG|NEG:QUANT|DOUBLE_NEG|COND:(IF|UNLESS|ELSE)"
    r"|TIMEREL:(BEFORE|AFTER|ON_OR_BEFORE|ON_OR_AFTER|ON|WITHIN))$"
)
NUMERIC_CODES = {
    "NUMBER_CHANGED",
    "CURRENCY_CHANGED",
    "CURRENCY_MISSING",
    "TIME_CHANGED",
    "FACT_MISSING",
    "VAGUE_QUANTITY_CHANGED",
}
NEGATION_CODES = {"NEGATION_DROPPED", "NEGATION_ADDED"}


def load(name):
    path = ROOT / "data" / name
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


SENTENCES = load("sentences.jsonl")
PAIRS = load("corruptions.jsonl")
DEV = [r for r in SENTENCES if r["split"] == "dev"]


def test_sizes():
    assert len(SENTENCES) >= 150
    assert sum(p["kind"] == "corruption" for p in PAIRS) >= 25
    assert sum(p["kind"] == "control" for p in PAIRS) >= 3


def test_sentence_schema_and_uniqueness():
    ids = [r["id"] for r in SENTENCES]
    assert ids == [f"s{i:03d}" for i in range(1, len(ids) + 1)]
    texts = [r["text"] for r in SENTENCES]
    assert len(texts) == len(set(texts))
    for row in SENTENCES:
        assert unicodedata.normalize("NFC", row["text"]) == row["text"] == row["text"].strip()
        assert row["split"] in {"dev", "heldout"}
        assert set(row["gold"]) == {"quantities", "rels", "times", "dates", "vague"}
        assert all(REL.match(r) for r in row["gold"]["rels"]), row["id"]
        for q in row["gold"]["quantities"]:
            assert q["type"] in {"COUNT", "MONEY", "PERCENT"}
            assert (q["currency"] is not None) == (q["type"] == "MONEY"), row["id"]
        if row["emotion"] is not None:
            assert row["emotion"] in EMOTIONS


def test_split_rule_is_deterministic_and_balanced():
    by_category = {}
    for row in SENTENCES:
        by_category.setdefault(row["category"], []).append(row)
    for category, rows in by_category.items():
        assert {r["split"] for r in rows} == {"dev", "heldout"}, category
        for index, row in enumerate(rows):
            assert (row["split"] == "heldout") == (index % 3 == 2), (category, row["id"])
    share = sum(r["split"] == "heldout" for r in SENTENCES) / len(SENTENCES)
    assert 0.28 <= share <= 0.36


def test_category_coverage():
    needed = {
        "money_basic",
        "money_scale",
        "currency_mix",
        "count_words",
        "percent",
        "change",
        "range",
        "bound",
        "timerel",
        "negation",
        "condition",
        "vague",
        "combined",
        "emotion",
        "sarcasm",
    }
    assert needed <= {r["category"] for r in SENTENCES}


def test_emotion_and_sarcasm_labels():
    labelled = [r for r in SENTENCES if r["emotion"] is not None]
    assert len(labelled) >= 40
    assert {r["emotion"] for r in labelled} == EMOTIONS
    sarcasm = [r["sarcasm"] for r in SENTENCES if r["category"] == "sarcasm"]
    assert True in sarcasm and False in sarcasm


@pytest.mark.parametrize("row", [r for r in DEV if not r["gold"]["dates"]], ids=lambda r: r["id"])
def test_lexer_finds_the_gold_quantities_on_dev(row):
    _, numeric = analyze(row["text"], "en")
    got = [(str(q.value), q.type, q.currency) for q in numeric.quantities]
    want = [(q["value"], q["type"], q["currency"]) for q in row["gold"]["quantities"]]
    assert got == want
    assert [t.text.casefold() for t in numeric.times] == [
        t.casefold() for t in row["gold"]["times"]
    ]
    assert len(numeric.vague) == row["gold"]["vague"]


def test_pair_schema():
    ids = [p["id"] for p in PAIRS]
    assert ids == [f"c{i:03d}" for i in range(1, len(ids) + 1)]
    by_id = {r["id"]: r for r in SENTENCES}
    for pair in PAIRS:
        assert by_id[pair["source_id"]]["text"] == pair["source"]
        for field in ("reference", "target"):
            assert DEVANAGARI.search(pair[field])
            assert unicodedata.normalize("NFC", pair[field]) == pair[field]
        assert pair["kind"] in {"corruption", "control"}
        assert pair["hindi_review"] in {"pending", "confirmed"}
        for code in pair["expected"]:
            assert code in SPEC, code
        if pair["kind"] == "corruption":
            assert pair["expected"] and pair["target"] != pair["reference"]
        else:
            assert pair["expected"] == []
        if pair["repairable"]:
            assert set(pair["expected"]) <= {
                "NUMBER_CHANGED",
                "CURRENCY_CHANGED",
                "CURRENCY_MISSING",
            }


def _signature(text):
    _, numeric = analyze(text, "hi")
    return (
        [(str(q.value), q.currency) for q in numeric.quantities],
        [(t.hour, t.minute) for t in numeric.times],
        [v.order for v in numeric.vague],
    )


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["id"])
def test_lexer_sees_each_numeric_corruption_and_no_false_alarm_on_controls(pair):
    same = _signature(pair["reference"]) == _signature(pair["target"])
    if pair["kind"] == "control":
        assert same
    elif set(pair["expected"]) & NUMERIC_CODES:
        assert not same
    else:
        assert same  # structure-level corruptions need the parser, not the lexer


@pytest.mark.parametrize(
    "pair", [p for p in PAIRS if set(p["expected"]) & NEGATION_CODES], ids=lambda p: p["id"]
)
def test_lexer_sees_negation_corruptions(pair):
    assert lex(pair["reference"], "hi").neg_count != lex(pair["target"], "hi").neg_count
