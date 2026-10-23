"""Highlighting, verdict and card data for the app (no Streamlit)."""

from types import SimpleNamespace

from meaningguard import render as R
from meaningguard.pipeline import Pipeline
from meaningguard.repair import Edit
from meaningguard.tone import ToneResult
from meaningguard.verifier import Finding, Report


def test_to_html_escapes_and_marks():
    html = R.to_html("a<b & c", [(1, 2, "error")])
    assert html == 'a<mark class="mg-error">&lt;</mark>b &amp; c'


def test_overlaps_keep_the_more_severe_mark_and_bounds_are_clipped():
    html = R.to_html("abcdef", [(0, 4, "info"), (2, 6, "error"), (-3, 1, "ok"), (5, 99, "warning")])
    assert html == '<mark class="mg-info">ab</mark>' + ('<mark class="mg-error">cdef</mark>')
    assert R.to_html("abc", []) == "abc" and R.to_html("", [(0, 2, "error")]) == ""


def test_edit_views_with_length_changes():
    # final text "A-XXX-B-YY": edit 1 turned "a" into "XXX" (+2), edit 2 turned "bbb" into "YY" (-1)
    edits = [Edit(6, 9, "bbb", "YY", "N"), Edit(2, 3, "a", "XXX", "N")]
    before, marks_before, marks_after = R.edit_views("A-XXX-B-YY", edits)
    assert before == "A-a-B-bbb"
    assert [before[a:b] for a, b, _ in marks_before] == ["a", "bbb"]
    final = "A-XXX-B-YY"
    assert [final[a:b] for a, b, _ in marks_after] == ["XXX", "YY"]
    assert {s for *_, s in marks_before} == {"error"} and {s for *_, s in marks_after} == {"ok"}


def test_edit_views_with_an_insertion():
    before, mb, ma = R.edit_views("₹500 दें", [Edit(0, 0, "", "₹", "CURRENCY_MISSING")])
    assert before == "500 दें" and mb == [(0, 0, "error")] and ma == [(0, 1, "ok")]


def test_a_real_repair_round_trip():
    r = Pipeline(None).check("Pay ₹500 by Friday.", "₹५५० शुक्रवार तक दें।")
    before, marks_before, marks_after = R.edit_views(r.final, r.repair.edits)
    assert before == "₹५५० शुक्रवार तक दें।" and before[1:4] == "५५०" and r.final[1:4] == "५००"
    assert R.repair_summary(r.repair) == [("NUMBER_CHANGED", "५५०", "५००")]
    cards = R.finding_cards(r.before)
    assert cards[0].code == "NUMBER_CHANGED" and cards[0].rows == [("value", "₹500", "₹550")]
    assert R.finding_cards(r.report) == []


def report_with(findings, source_tier=1, target_tier=1):
    return Report(findings, [], source_tier, target_tier)


def test_verdict_labels_and_notes():
    assert R.verdict(report_with([])).title == "Meaning preserved"
    err = Finding("FACT_MISSING", "error", "m")
    assert R.verdict(report_with([err])).status == "fail"
    low = R.verdict(report_with([], target_tier=3))
    assert low.basic and "Basic check" in low.note
    mid = R.verdict(report_with([], target_tier=2))
    assert not mid.basic and "Partly parsed" in mid.note


def test_finding_marks_use_the_fact_spans_and_skip_tone_findings():
    a = Finding("NUMBER_CHANGED", "error", "m", source={"span": [1, 4]}, target={"span": [2, 5]})
    tone = Finding("TONE_UNCERTAIN", "info", "m")
    assert R.finding_marks(report_with([a, tone])) == ([(1, 4, "error")], [(2, 5, "error")])


def test_cards_are_sorted_by_severity():
    cards = R.finding_cards(
        report_with([Finding("UNVERIFIED", "info", "i"), Finding("FACT_MISSING", "error", "e")])
    )
    assert [c.severity for c in cards] == ["error", "info"]


def test_tone_rows():
    assert R.tone_rows(None) is None
    rec = {
        "sentiment": "negative",
        "label": "anger",
        "confidence": 0.8,
        "possible_sarcasm": False,
        "signals": [
            {"name": "sentiment:xlmr", "label": "negative", "prob": 0.9, "sentiment": "negative"}
        ],
    }
    tone = ToneResult(rec, None, [Finding("TONE_UNCERTAIN", "info", "unsure")], ["note"])
    rows = R.tone_rows(tone)
    assert rows["sides"][0][1]["signals"] == [("sentiment:xlmr", "negative", 0.9)]
    assert rows["sides"][1] == ("Hindi translation", None)
    assert rows["notes"] == ["note"] and rows["findings"][0].code == "TONE_UNCERTAIN"


def test_repair_summary_orders_by_position():
    repair = SimpleNamespace(edits=[Edit(5, 6, "b", "B", "X"), Edit(1, 2, "a", "A", "X")])
    assert [o for _, o, _ in R.repair_summary(repair)] == ["a", "b"]


def test_verdict_html():
    html = R.verdict_html(R.verdict(report_with([])), fixed=2)
    assert (
        "mg-status-pass" in html and "✓" in html and "2 wrong values fixed automatically." in html
    )
    err = R.verdict(report_with([Finding("FACT_MISSING", "error", "m")]))
    assert "mg-status-fail" in R.verdict_html(err) and "mg-vs" not in R.verdict_html(err)
    assert "1 wrong value fixed" in R.verdict_html(R.verdict(report_with([])), fixed=1)


def test_sheet_html_escapes_and_sets_language():
    html = R.sheet_html("Hindi <x>", "a<b", [(1, 2, "error")], "hi")
    assert 'lang="hi"' in html and "Hindi &lt;x&gt;" in html and "a<mark" in html


def test_card_html_reads_like_a_diagnostic():
    c = R.Card(
        "NUMBER_CHANGED", "error", "₹500 became ₹550 <!>", [("value", "₹500", "₹550")], "high"
    )
    html = R.card_html(c)
    assert "error[NUMBER_CHANGED]" in html and "mg-sev-error" in html and "&lt;!&gt;" in html
    assert "<b>₹500</b>" in html and 'class="mg-bad"' in html
    assert "mg-pair" not in R.card_html(R.Card("UNVERIFIED", "info", "m", [], "low"))


def test_tone_side_html():
    side = {
        "sentiment": "negative",
        "emotion": "anger",
        "confidence": 0.8,
        "sarcasm": True,
        "signals": [],
    }
    html = R.tone_side_html("English source", side)
    assert "mg-s-negative" in html and "possible sarcasm" in html and "width:80%" in html
    assert "sentiment: negative" in html and "emotion: anger" in html
    assert "possible sarcasm" not in R.tone_side_html("x", {**side, "sarcasm": None})
    assert "not available" in R.tone_side_html("Hindi translation", None)


def test_tone_side_shows_the_emotion_distribution():
    side = {
        "sentiment": "negative",
        "emotion": "uncertain",
        "emotions": [("fear", 0.44), ("joy", 0.3)],
        "confidence": 0.5,
        "sarcasm": False,
        "signals": [],
    }
    html = R.tone_side_html("English source", side)
    assert "emotion: fear (low confidence)" in html and "44%" in html and "30%" in html
