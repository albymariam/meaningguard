"""Verifier: spec cases, the corruption set, and the behaviours that need their own test."""

import json
from pathlib import Path

import pytest

from meaningguard.ir import compile_text
from meaningguard.verifier import verify

ROOT = Path(__file__).resolve().parent.parent


def load(path):
    return [json.loads(x) for x in (ROOT / path).read_text("utf-8").splitlines() if x.strip()]


SPEC = load("tests/spec_cases/verifier.jsonl")
PAIRS = load("data/corruptions.jsonl")
SENTENCES = {r["id"]: r for r in load("data/sentences.jsonl")}
DEV_PAIRS = [p for p in PAIRS if SENTENCES[p["source_id"]]["split"] == "dev"]


def run(source, target):
    return verify(compile_text(source, "en"), compile_text(target, "hi"))


@pytest.mark.parametrize("row", SPEC, ids=lambda r: r["id"])
def test_spec_case(row):
    report = run(row["input"]["source"], row["input"]["target"])
    assert sorted(report.codes()) == sorted(row["expect"]["findings"])


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p["id"])
def test_corruption_pairs(pair):
    """Each corruption gives its expected finding; each control gives none."""
    report = run(pair["source"], pair["target"])
    got = sorted({f.code for f in report.findings if f.severity != "info"})
    assert got == sorted(pair["expected"])


@pytest.mark.parametrize("pair", DEV_PAIRS, ids=lambda p: p["id"])
def test_reference_translation_has_no_errors(pair):
    """The reference Hindi (the correct translation behind each pair) verifies clean."""
    report = run(pair["source"], pair["reference"])
    assert report.status == "pass", report.codes()


def test_status_and_confidence():
    ok = run("Pay ₹500.", "₹५०० दें।")
    assert (ok.status, ok.confidence, ok.findings) == ("pass", "high", [])
    bad = run("Pay ₹500.", "₹५०० से ज़्यादा दें।")
    assert bad.status == "fail"
    warn = run("Pay about ₹500.", "₹५०० दें।")
    assert warn.status == "warn"
    assert run("Hello there.", "नमस्ते।").confidence == "low"


def test_findings_carry_both_facts_and_a_message():
    f = run("Pay ₹500.", "₹६०० दें।").findings[0]
    assert f.code == "NUMBER_CHANGED" and f.severity == "error"
    assert f.source["value"]["amount"] == "500" and f.target["value"]["amount"] == "600"
    assert "500" in f.message and "600" in f.message
    assert [d.path for d in f.diffs] == ["value"]


def test_several_differences_in_one_fact_give_the_most_specific_code():
    f = run("Pay $500.", "₹६०० दें।").findings[0]
    assert f.code == "CURRENCY_CHANGED"
    assert {d.code for d in f.diffs} == {"CURRENCY_CHANGED", "NUMBER_CHANGED"}


def test_only_the_wrong_field_of_a_change_is_reported():
    f = run("The price fell from ₹500 to ₹400.", "कीमत ₹५०० से घटकर ₹४५० हो गई।").findings[0]
    assert f.code == "NUMBER_CHANGED" and [d.path for d in f.diffs] == ["to"]


def test_verb_that_contradicts_the_numbers_is_a_direction_change():
    report = run("The price fell from ₹500 to ₹400.", "कीमत ₹५०० से बढ़कर ₹४०० हो गई।")
    assert report.codes() == ["DIRECTION_CHANGED"]


def test_inconsistent_source_is_reported_and_the_numbers_are_trusted():
    report = run("Prices rose from ₹500 to ₹400.", "कीमत ₹५०० से घटकर ₹४०० हो गई।")
    assert report.codes() == ["SOURCE_INCONSISTENT"] and report.status == "warn"


def test_reordered_clauses_still_match():
    report = run("Pay ₹500 if it rains.", "अगर बारिश हो तो ₹५०० दें।")
    assert report.status == "pass", report.codes()


def test_unless_and_if_not_agree():
    assert (
        run("Unless you pay, ₹500 is due.", "अगर आप भुगतान नहीं करते हैं तो ₹५०० देय है।").status == "pass"
    )


def test_double_negation_is_uncertain_not_an_error():
    report = run("Do not pay.", "भुगतान न करना गलत नहीं है।")
    assert report.codes() == ["NEGATION_UNCERTAIN"] and report.status == "warn"


def test_lost_clause_numbers_are_still_found_when_the_target_is_unparsed():
    """Tier 3 target: only numbers and polarity are compared, and a lost number is a warning."""
    report = run("There are twenty-five seats left.", "पँचिश सीटें बची हैं।")
    assert report.codes() == ["FACT_MISSING"] and report.confidence == "low"
    assert report.findings[0].severity == "warning"


def test_partly_parsed_target_downgrades_missing_to_unverified():
    report = run("Pay ₹500 by Friday.", "₹५०० के बाद दें")  # 'बाद' has no anchor: tier 2
    assert report.codes() == ["UNVERIFIED"] and report.status == "pass"
    assert report.confidence == "medium"


def test_clause_count_change_is_reported():
    report = run("Pay ₹500. Pay ₹600.", "₹५०० दें।")
    assert report.codes() == ["CLAUSE_COUNT_CHANGED", "FACT_MISSING"]


def test_report_to_dict_is_json():
    report = run("Pay ₹500.", "₹६०० दें।")
    assert json.loads(json.dumps(report.to_dict()))["status"] == "fail"


def test_matched_facts_are_listed_for_repair_checks():
    report = run("The fee is ₹500 and the fine is ₹200.", "शुल्क ₹५०० है और जुर्माना ₹३०० है।")
    assert len(report.matched) == 1


def test_every_finding_code_is_in_the_spec():
    from meaningguard.verifier import SEVERITY

    spec = (ROOT / "docs" / "spec" / "06_verification_repair.md").read_text("utf-8")
    for code in SEVERITY:
        assert f"`{code}`" in spec, code
