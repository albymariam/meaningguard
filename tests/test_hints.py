"""Hints name a concrete fix and never touch the text."""

import pytest

from meaningguard import render as R
from meaningguard.hints import hint
from meaningguard.pipeline import Pipeline

CASES = [
    ("Do not pay in cash.", "नकद में भुगतान करें।", "NEGATION_DROPPED", "न / नहीं"),
    ("Pay before Monday.", "सोमवार से पहले भुगतान न करें।", "NEGATION_ADDED", "Remove the extra"),
    (
        "The price fell from ₹500 to ₹400.",
        "कीमत ₹500 से बढ़कर ₹400 हो गई।",
        "DIRECTION_CHANGED",
        "घटना",
    ),
    ("Submit by Friday.", "शुक्रवार के बाद जमा करें।", "TIMEREL_CHANGED", "तक"),
    ("Pay at least ₹500.", "अधिकतम ₹500 का भुगतान करें।", "COMPARISON_CHANGED", "कम से कम"),
    ("The fine is ₹200.", "जुर्माना लगेगा।", "FACT_MISSING", "₹200"),
    ("The meeting starts at 10:30 AM.", "बैठक सुबह 10:45 बजे शुरू होगी।", "TIME_CHANGED", "10:30"),
]


@pytest.mark.parametrize("source,target,code,word", CASES)
def test_hint_for_each_error(source, target, code, word):
    report = Pipeline(None).check(source, target).report
    finding = next(f for f in report.findings if f.code == code)
    assert word in hint(finding)
    card = next(c for c in R.finding_cards(report) if c.code == code)
    assert card.hint == hint(finding) and "Suggested fix" in R.card_html(card)


def test_no_hint_for_a_clean_card():
    assert "Suggested fix" not in R.card_html(R.Card("X", "info", "m", [], "low"))


def test_hints_do_not_change_the_text():
    r = Pipeline(None).check("Do not pay in cash.", "नकद में भुगतान करें।")
    assert r.final == r.translation
