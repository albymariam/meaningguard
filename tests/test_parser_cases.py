"""Runs tests/spec_cases/parser.jsonl through the full front end (lexer to IR)."""

import json
from pathlib import Path

import pytest

from meaningguard.ir import compile_text

CASES = Path(__file__).resolve().parent / "spec_cases" / "parser.jsonl"
ROWS = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def subset(expected, actual) -> bool:
    """Every key of ``expected`` is present in ``actual`` with an equal (recursively) value."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset(v, actual[k]) for k, v in expected.items()
        )
    return expected == actual


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_parser_case(row):
    ir = compile_text(row["input"], row["lang"])
    expect = row["expect"]
    assert ir.tier == expect["tier"], ir.to_json()
    facts = ir.facts()
    assert len(facts) == len(expect["facts"]), [f["kind"] for f in facts]
    for want, got in zip(expect["facts"], facts, strict=True):
        assert subset(want, got), (want, got)
    for code in expect.get("flags", []) + expect.get("diagnostics", []):
        assert code in ir.codes(), (code, sorted(ir.codes()))
    if "cond" in expect:
        assert any(c["cond"] and c["cond"]["op"] == expect["cond"] for c in ir.clauses)
    if "clause_polarities" in expect:
        assert [c["polarity"] for c in ir.clauses] == expect["clause_polarities"]
