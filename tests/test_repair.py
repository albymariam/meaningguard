"""Repair: spec cases, the corruption set, notation rules, and the re-verification checks."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from meaningguard import numerics as num
from meaningguard.ir import compile_text
from meaningguard.repair import _safe, render_currency, render_number, repair
from meaningguard.verifier import Finding, Report

ROOT = Path(__file__).resolve().parent.parent


def load(path):
    return [json.loads(x) for x in (ROOT / path).read_text("utf-8").splitlines() if x.strip()]


SPEC = load("tests/spec_cases/repair.jsonl")
PAIRS = load("data/corruptions.jsonl")


def fix(source, target):
    return repair(compile_text(source, "en"), target)


@pytest.mark.parametrize("row", SPEC, ids=lambda r: r["id"])
def test_spec_case(row):
    result = fix(row["input"]["source"], row["input"]["target"])
    expect = row["expect"]
    assert result.repaired == expect["repaired"]
    assert sorted(result.after.codes()) == sorted(expect["findings_after"])
    if "repaired_target" in expect:
        assert result.text == expect["repaired_target"]


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["id"])
def test_corruption_pairs(pair):
    """Repairable corruptions end with no finding; the rest are left exactly as translated."""
    result = fix(pair["source"], pair["target"])
    if pair["repairable"]:
        assert result.repaired and result.after.codes() == [], result.text
    else:
        assert not result.repaired and result.text == compile_text(pair["target"], "hi").text


@pytest.mark.parametrize("pair", [p for p in PAIRS if p["repairable"]], ids=lambda p: p["id"])
def test_repair_is_idempotent_and_touches_only_the_target(pair):
    source = compile_text(pair["source"], "en")
    first = repair(source, pair["target"])
    second = repair(source, first.text)
    assert not second.repaired and second.text == first.text
    for edit in first.edits:  # each edit sits inside the original target text
        assert 0 <= edit.start <= edit.end <= len(compile_text(pair["target"], "hi").text)


@pytest.mark.parametrize(
    "source,target,expected",
    [
        ("Pay within 7 days.", "10 दिनों के भीतर भुगतान करें।", "7 दिनों के भीतर भुगतान करें।"),
        (
            "Sales rose 12.5% last year.",
            "पिछले साल बिक्री में 15.2% की वृद्धि हुई।",
            "पिछले साल बिक्री में 12.5% की वृद्धि हुई।",
        ),
        ("Pay ₹5 lakh.", "₹५ करोड़ दें।", "₹५ लाख दें।"),
        ("Pay ₹500 and ₹900.", "₹५०० और ₹८०० दें।", "₹५०० और ₹९०० दें।"),
        ("The loan amount is ₹12.5 lakh.", "ऋण राशि ₹1.25 लाख है।", "ऋण राशि ₹12.5 लाख है।"),
        (
            "Rent is 15,000 rupees per month.",
            "किराया प्रति माह 15,000 डॉलर है।",
            "किराया प्रति माह 15,000 रुपये है।",
        ),
        (
            "The deposit is Rs 10,000 and is refundable.",
            "जमा राशि 10,000 है और वापसी योग्य है।",
            "जमा राशि 10,000 रुपये है और वापसी योग्य है।",
        ),
    ],
)
def test_notation_of_the_target_is_kept(source, target, expected):
    assert fix(source, target).text == expected


def test_only_the_wrong_field_is_edited():
    result = fix("The price fell from ₹500 to ₹400.", "कीमत ₹५०० से घटकर ₹४५० हो गई।")
    assert [(e.old, e.new) for e in result.edits] == [("४५०", "४००")]


def test_two_wrong_numbers_are_repaired_in_one_pass():
    result = fix("Deposit between ₹5 lakh and ₹10 lakh.", "जमा राशि ₹५ लाख और ₹१ लाख के बीच रखें।")
    assert result.after.codes() == [] and result.repaired


@pytest.mark.parametrize(
    "source,target",
    [
        ("Pay ₹500.", "₹६०० ₹७०० दें।"),  # two candidates
        ("Take 1.500 km.", "१,५ किमी लें।"),  # ambiguous separators
        ("Pay ₹500.", "₹५०० दें और ₹५०० दें।"),
        ("Pay 50%.", "५० दें।"),  # the % sign would have to be added
        ("Pay within 7 days.", "7 हफ्तों के भीतर भुगतान करें।"),  # the unit differs
    ],
)
def test_no_repair_when_a_precondition_fails(source, target):
    assert not fix(source, target).repaired


def test_source_that_is_only_partly_parsed_is_never_repaired():
    source = compile_text("Pay ₹500 after.", "en")  # 'after' has no anchor: tier 2
    assert source.tier == 2
    result = repair(source, "₹४०० दें।")
    assert not result.repaired and result.after.codes() == ["NUMBER_CHANGED"]


def _report(findings, matched=()):
    return Report(list(findings), list(matched), 1, 1)


def _finding(code="NUMBER_CHANGED", fp="a", severity="error"):
    return Finding(code, severity, "m", 0, {"fp": fp, "kind": "QUANT"}, {"fp": "b"})


def test_safety_check_rejects_an_edit_that_leaves_the_finding():
    finding = _finding()
    assert not _safe(_report([finding]), _report([finding]), finding)


def test_safety_check_rejects_a_new_error():
    finding, other = _finding(), _finding("FACT_MISSING", fp="c")
    assert not _safe(_report([finding]), _report([other]), finding)


def test_safety_check_rejects_a_lost_match():
    finding = _finding()
    assert not _safe(_report([finding], [("x", None)]), _report([]), finding)


def test_safety_check_accepts_a_clean_fix():
    finding = _finding()
    assert _safe(
        _report([finding], [("x", None)]), _report([], [("x", None), ("a", None)]), finding
    )


def test_a_group_that_does_not_verify_is_reverted(monkeypatch):
    """Force the safety check to fail: the text must come back unchanged with REPAIR_REVERTED."""
    from meaningguard import repair as module

    monkeypatch.setattr(module, "_safe", lambda *a: False)
    result = fix("Pay ₹500.", "₹६०० दें।")
    assert not result.repaired and result.text == "₹६०० दें।"
    assert result.reverted and result.reverted[0]["code"] == "REPAIR_REVERTED"


def test_result_to_dict_is_json():
    result = fix("Pay ₹500.", "₹६०० दें।")
    data = json.loads(json.dumps(result.to_dict()))
    assert data["repaired"] and data["edits"][0]["new"] == "५००"


# -- the number writer -------------------------------------------------------------------------


def tokens(text):
    res, nres = num.analyze(text, "hi")
    item = next(i for i in nres.items if i.kind == "QUANTITY")
    return res.tokens[item.start : item.end]


@pytest.mark.parametrize(
    "amount,target,currency,expected",
    [
        ("500000", "५०,००० रुपये", "INR", "५,००,०००"),
        ("500000", "50,000 rupees", "INR", "5,00,000"),
        ("500000", "$50,000", "USD", "500,000"),
        ("500000", "50000 रुपये", "INR", "500000"),
        ("500000", "५० लाख", "INR", "५ लाख"),
        ("1250000", "5 लाख", "INR", "12.5 लाख"),
        ("500000", "5 करोड़", "INR", "5 लाख"),
        ("12.5", "15.2%", None, "12.5"),
        ("42", "पाँच", None, "42"),
    ],
)
def test_render_number(amount, target, currency, expected):
    assert render_number(Decimal(amount), tokens(target), currency, False) == expected


def test_render_number_gives_up_on_two_scales_or_a_negative():
    assert render_number(Decimal("5"), tokens("5 लाख 20 हज़ार"), "INR", False) is None
    assert render_number(Decimal("-5"), tokens("५"), None, False) is None


def test_render_currency():
    assert render_currency("INR", "symbol", Decimal(5)) == "₹"
    assert render_currency("INR", "word", Decimal(5)) == "रुपये"
    assert render_currency("INR", "word", Decimal(1)) == "रुपया"
    assert render_currency("USD", "code", Decimal(5)) == "USD"
    assert render_currency("XYZ", "word", Decimal(5)) is None
