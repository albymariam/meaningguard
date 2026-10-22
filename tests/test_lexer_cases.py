"""Runs the Phase 1 spec cases for the lexer, number assembly and negation counts."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from meaningguard.lexer import EmptyInput, InputTooLong, lex
from meaningguard.numerics import analyze

CASES = Path(__file__).resolve().parent / "spec_cases"


def _load(name):
    rows = []
    for line in (CASES / name).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


LEXER_ROWS = _load("lexer_numbers.jsonl")
NEGATION_ROWS = _load("negation.jsonl")
ERRORS = {"EmptyInput": EmptyInput, "InputTooLong": InputTooLong}


def _text(row):
    if row["input"] is None:
        gen = row["generate"]
        return gen["char"] * gen["count"]
    return row["input"]


@pytest.mark.parametrize("row", LEXER_ROWS, ids=[r["id"] for r in LEXER_ROWS])
def test_lexer_and_number_cases(row):
    expect = row["expect"]
    text = _text(row)
    code = expect.get("error")
    if code in ERRORS:
        with pytest.raises(ERRORS[code]):
            lex(text)
        return
    lexed, numeric = analyze(text, row["lang"])
    if code:
        assert code in [e.code for e in numeric.errors]
    else:
        assert not numeric.errors
    if "quantities" in expect:
        got = numeric.quantities
        assert len(got) == len(expect["quantities"]), (got, expect["quantities"])
        for q, want in zip(got, expect["quantities"], strict=True):
            assert q.value == Decimal(want["value"])
            assert q.currency == want["currency"]
            assert sorted(q.flags) == sorted(want["flags"])
            if "type" in want:
                assert q.type == want["type"]
            elif want["currency"]:
                assert q.type == "MONEY"
    if "times" in expect:
        assert [t.text for t in numeric.times] == expect["times"]
    if "vague" in expect:
        assert len(numeric.vague) == expect["vague"]
    if "sentence_count" in expect:
        assert lexed.sentence_count == expect["sentence_count"]
    if "neg_count" in expect:
        assert lexed.neg_count == expect["neg_count"]
    for flag in expect.get("flags", []):
        assert flag in numeric.flags


@pytest.mark.parametrize("row", NEGATION_ROWS, ids=[r["id"] for r in NEGATION_ROWS])
def test_negation_counts_and_conditions(row):
    expect = row["expect"]
    lexed = lex(row["input"], row["lang"])
    assert lexed.neg_count == expect["neg_count"]
    if expect.get("cond") == "ELSE":
        assert any(t.attrs.get("role") == "COND_ELSE" for t in lexed.of_type("REL"))
