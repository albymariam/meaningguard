"""Schema and consistency tests for the Phase 1 spec case files."""

import json
import re
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from tests.oracle import eval_english, eval_hindi

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "spec_cases"
SPEC_TEXT = "\n".join(
    p.read_text(encoding="utf-8") for p in sorted((ROOT / "docs" / "spec").iterdir())
).casefold()
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
CODE = re.compile(r"^[A-Za-z][A-Za-z_]+$")

AREAS = {"lexer", "numbers", "negation", "parser", "semantics", "verifier", "repair"}
EXPECT_KEYS = {
    "lexer": {"quantities", "neg_count", "sentence_count", "error"},
    "numbers": {"quantities", "error", "flags", "times", "vague", "sentence_count"},
    "negation": {"polarity", "neg_count", "scopes", "cond", "flags", "strength"},
    "parser": {"tier", "facts", "flags", "diagnostics", "cond", "clause_polarities"},
    "semantics": {"equivalent"},
    "verifier": {"findings"},
    "repair": {"repaired", "repaired_target", "findings_after"},
}


def load_all():
    rows = []
    for path in sorted(CASES.glob("*.jsonl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                row = json.loads(line)
                row["_where"] = f"{path.name}:{number}"
                rows.append(row)
    return rows


ROWS = load_all()


def test_enough_cases():
    assert len(ROWS) >= 140
    assert {r["area"] for r in ROWS} == AREAS


def test_ids_unique_and_well_formed():
    ids = [r["id"] for r in ROWS]
    assert len(ids) == len(set(ids))
    for row in ROWS:
        area, _, number = row["id"].partition("-")
        assert area == row["area"] and number.isdigit() and len(number) == 3, row["id"]


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_case_shape(row):
    for field in ("id", "area", "lang", "input", "expect", "must", "rule"):
        assert field in row, (row["id"], field)
    assert row["area"] in AREAS
    assert row["lang"] in {"en", "hi", "pair"}
    assert row["must"] in {"pass", "fail"}
    assert isinstance(row["expect"], dict) and row["expect"]
    assert set(row["expect"]) <= EXPECT_KEYS[row["area"]], row["expect"].keys()
    if row["input"] is None:
        assert "generate" in row
    elif row["lang"] == "pair":
        assert set(row["input"]) == {"source", "target"} | (
            {"source_lang", "target_lang"} & set(row["input"])
        )
    else:
        assert isinstance(row["input"], str)


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_rule_is_defined_in_the_spec(row):
    assert row["rule"].casefold() in SPEC_TEXT, row["rule"]


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_every_code_in_expect_is_defined_in_the_spec(row):
    expect = row["expect"]
    codes = []
    for key in ("flags", "diagnostics", "findings", "findings_after"):
        codes += expect.get(key, [])
    if isinstance(expect.get("error"), str):
        codes.append(expect["error"])
    for item in expect.get("quantities", []):
        codes += item.get("flags", [])
    for code in codes:
        assert CODE.match(code), code
        assert code.casefold() in SPEC_TEXT, f"{code} is not defined in docs/spec"


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_language_tag_matches_script(row):
    if row["lang"] == "hi":
        assert DEVANAGARI.search(row["input"]), row["id"]
    elif row["lang"] == "en" and isinstance(row["input"], str) and row["input"].strip():
        letters = re.sub(r"[^A-Za-zऀ-ॿ]", "", row["input"])
        assert not DEVANAGARI.search(letters), row["id"]
    elif row["lang"] == "pair":
        pair = row["input"]
        for side in ("source", "target"):
            lang = pair.get(f"{side}_lang") or ("en" if side == "source" else "hi")
            has = bool(DEVANAGARI.search(pair[side]))
            assert has == (lang == "hi"), (row["id"], side)


def test_quantities_are_valid_decimals_with_known_currency():
    for row in ROWS:
        for item in row["expect"].get("quantities", []):
            Decimal(item["value"])
            assert item["currency"] in {None, "INR", "USD", "EUR", "GBP", "JPY"}
            assert item.get("type", "COUNT") in {"COUNT", "MONEY", "PERCENT"}


def test_fail_cases_expect_a_rejection():
    for row in ROWS:
        if row["must"] == "fail" and row["area"] in {"numbers", "lexer"}:
            assert "error" in row["expect"], row["id"]
        if row["must"] == "fail" and row["area"] == "semantics":
            assert row["expect"]["equivalent"] is False, row["id"]


def test_ids_in_each_area_are_consecutive():
    by_area = {}
    for row in ROWS:
        by_area.setdefault(row["area"], []).append(int(row["id"].split("-")[1]))
    for area, numbers in by_area.items():
        assert sorted(numbers) == list(range(1, len(numbers) + 1)), area


HINDI_ORACLE = [
    ("सवा लाख", "125000"),
    ("सवा सौ", "125"),
    ("सवा तीन", "3.25"),
    ("सवा दो सौ", "225"),
    ("साढ़े तीन सौ", "350"),
    ("साढ़े चार लाख", "450000"),
    ("पौने चार", "3.75"),
    ("पौने दो सौ", "175"),
    ("पौने चार सौ", "375"),
    ("डेढ़ करोड़", "15000000"),
    ("ढाई हज़ार", "2500"),
    ("ढाई हजार", "2500"),
    ("डेढ़", "1.5"),
    ("आधा लाख", "50000"),
    ("दो करोड़ पचास लाख", "25000000"),
    ("अट्ठाईस", "28"),
    ("पाँच सौ पाँच", "505"),
    ("एक सौ पाँच", "105"),
    ("पाँच हजार", "5000"),
    ("निन्यानवे", "99"),
]
ENGLISH_ORACLE = [
    ("twenty-five", "25"),
    ("twenty five", "25"),
    ("five hundred and five", "505"),
    ("one hundred and five", "105"),
    ("half a million", "500000"),
    ("a quarter of a crore", "2500000"),
    ("two crore fifty lakh", "25000000"),
    ("a hundred", "100"),
    ("three quarters", "0.75"),
]


@pytest.mark.parametrize("phrase,expected", HINDI_ORACLE)
def test_hindi_number_rules_against_oracle(phrase, expected):
    assert eval_hindi(phrase) == Fraction(expected)


@pytest.mark.parametrize("phrase,expected", ENGLISH_ORACLE)
def test_english_number_rules_against_oracle(phrase, expected):
    assert eval_english(phrase) == Fraction(expected)


def test_oracle_rejects_unknown_words():
    with pytest.raises(ValueError):
        eval_hindi("सवा कुछ")


def test_number_cases_with_words_match_the_oracle():
    """Cross-check word-based expectations in numbers-0xx against the oracle."""
    expected = {
        "numbers-013": ("सवा लाख", "125000"),
        "numbers-014": ("साढ़े तीन सौ", "350"),
        "numbers-015": ("पौने चार सौ", "375"),
        "numbers-016": ("डेढ़ करोड़", "15000000"),
        "numbers-017": ("ढाई हज़ार", "2500"),
        "numbers-018": ("दो करोड़ पचास लाख", "25000000"),
        "numbers-019": ("अट्ठाईस", "28"),
    }
    by_id = {r["id"]: r for r in ROWS}
    for case_id, (phrase, value) in expected.items():
        assert phrase in by_id[case_id]["input"], case_id
        assert by_id[case_id]["expect"]["quantities"][0]["value"] == value
        assert eval_hindi(phrase) == Fraction(value)
