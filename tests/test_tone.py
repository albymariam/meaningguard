"""Tone findings, the tone checker's degradation and its place in the pipeline (no models)."""

import pytest

from meaningguard.config import EmotionSettings
from meaningguard.emotion import EmotionAnalyzer
from meaningguard.pipeline import Pipeline
from meaningguard.tone import ToneChecker
from meaningguard.verifier import SEVERITY, TONE_CODES, compare_tone


def rec(sentiment, label="uncertain", conf=0.8, sarcasm=False):
    return {"sentiment": sentiment, "label": label, "confidence": conf, "possible_sarcasm": sarcasm}


@pytest.mark.parametrize(
    "src,tgt,codes",
    [
        (rec("positive"), rec("negative"), ["TONE_POLARITY_FLIPPED"]),
        (rec("negative"), rec("positive"), ["TONE_POLARITY_FLIPPED"]),
        (rec("negative"), rec("negative"), []),
        (rec("neutral"), rec("negative", conf=0.9), ["TONE_INTENSIFIED"]),
        (rec("neutral"), rec("negative", conf=0.5), []),
        (rec("positive"), rec("neutral", conf=0.9), ["TONE_WEAKENED"]),
        (rec("positive"), rec("neutral", conf=0.5), []),
        (rec("negative"), rec("uncertain", conf=0.0), ["TONE_UNCERTAIN"]),
        (rec("uncertain"), rec("uncertain"), []),
        (rec("neutral"), rec("uncertain"), []),
        (rec("negative", "anger"), rec("negative", "sadness"), ["EMOTION_CHANGED"]),
        (rec("negative", "anger"), rec("negative", "neutral"), []),
        (rec("negative", "anger"), rec("negative", "anger"), []),
        (rec("uncertain", sarcasm=True), rec("positive"), ["SARCASM_POSSIBLY_LOST"]),
        (rec("uncertain", sarcasm=True), rec("negative"), []),
        (rec("positive", sarcasm=True), rec("negative"), []),  # the surface sentiment is ignored
    ],
)
def test_compare_tone(src, tgt, codes):
    assert [f.code for f in compare_tone(src, tgt)] == codes


def test_missing_records_give_nothing():
    assert compare_tone(None, rec("positive")) == [] and compare_tone(rec("positive"), None) == []


def test_tone_findings_are_advisory():
    assert set(TONE_CODES) <= set(SEVERITY)
    assert all(SEVERITY[c] != "error" for c in TONE_CODES)
    [f] = compare_tone(rec("positive"), rec("negative"))
    assert f.confidence == "low" and f.source is None and f.diffs == [] and not f.unique


# -- the checker -------------------------------------------------------------------------------


class Clf:
    def __init__(self, scores=None, error=None):
        self.scores, self.error = scores or {}, error

    def classify(self, text):
        if self.error:
            raise self.error
        return self.scores


class Back:
    def __init__(self, answer="I am angry", error=None):
        self.answer, self.error, self.closed = answer, error, False

    def translate(self, sentences):
        if self.error:
            raise self.error
        return [self.answer]

    def close(self):
        self.closed = True


def analyzer(sentiment=None, emotion=None, irony=None):
    pos = {"positive": 0.9, "neutral": 0.05, "negative": 0.05}
    joy = {"joy": 0.8, "neutral": 0.2}
    calm = {"non_irony": 0.9, "irony": 0.1}
    return EmotionAnalyzer(
        sentiment or Clf(pos), emotion or Clf(joy), irony or Clf(calm), EmotionSettings()
    )


def test_checker_finds_a_flip():
    neg = {"negative": 0.9, "neutral": 0.05, "positive": 0.05}
    anger = {"anger": 0.8, "neutral": 0.2}

    class Switch(Clf):  # positive in English source, negative on the Hindi and its back-translation
        def classify(self, text):
            return neg if text != "I am happy" else {"positive": 0.9, "neutral": 0.1}

    class Feel(Clf):
        def classify(self, text):
            return anger if text != "I am happy" else {"joy": 0.8, "neutral": 0.2}

    checker = ToneChecker(analyzer(Switch(), Feel()), Back())
    out = checker.check("I am happy", "मैं गुस्से में हूँ")
    assert [f.code for f in out.findings] == ["TONE_POLARITY_FLIPPED"] and out.notes == []
    assert out.source["sentiment"] == "positive" and out.target["sentiment"] == "negative"
    assert out.to_dict()["findings"][0]["code"] == "TONE_POLARITY_FLIPPED"


def test_back_translation_failure_is_noted_not_raised():
    out = ToneChecker(analyzer(), Back(error=RuntimeError("down"))).check("Nice.", "अच्छा।")
    assert out.source is not None and out.target["sentiment"] == "uncertain"
    assert any("back-translation failed" in n for n in out.notes)
    assert any("Hindi text only" in n for n in out.notes)


def test_no_back_translator_and_empty_answer():
    assert any("Hindi text only" in n for n in ToneChecker(analyzer()).check("a", "अ").notes)
    out = ToneChecker(analyzer(), Back(answer="  ")).check("a", "अ")
    assert any("returned no text" in n for n in out.notes)


def test_model_failure_degrades():
    bad = analyzer(sentiment=Clf(error=RuntimeError("boom")))
    out = ToneChecker(bad).check("a", "अ")
    assert (out.source, out.target, out.findings) == (None, None, [])
    assert "source tone unavailable" in out.notes[0]


def test_close_reaches_the_back_translator():
    back = Back()
    ToneChecker(analyzer(), back).close()
    assert back.closed


# -- the pipeline ------------------------------------------------------------------------------


class Fixed:
    """A tone checker that answers with ready records."""

    def __init__(self, src, tgt, error=None):
        self.src, self.tgt, self.error, self.seen, self.closed = src, tgt, error, [], False

    def check(self, source, target):
        self.seen.append((source, target))
        if self.error:
            raise self.error
        from meaningguard.tone import ToneResult

        return ToneResult(self.src, self.tgt, compare_tone(self.src, self.tgt), [])

    def close(self):
        self.closed = True


def test_tone_is_off_by_default():
    r = Pipeline(None).check("Pay ₹500.", "₹500 दें।")
    assert r.tone is None and r.to_dict()["tone"] is None and "tone_ms" not in r.timings


def test_tone_runs_on_the_repaired_text_and_joins_the_report():
    tone = Fixed(rec("positive"), rec("negative"))
    r = Pipeline(None, tone=tone).check("Pay ₹500 now.", "₹5000 अभी दें।")
    assert tone.seen == [("Pay ₹500 now.", r.final)] and "500" in r.final and "5000" not in r.final
    assert "TONE_POLARITY_FLIPPED" in r.report.codes() and r.report.status == "warn"
    assert "TONE_POLARITY_FLIPPED" not in r.before.codes()
    assert (
        r.source_ir.emotion["sentiment"]
        == "positive"
        == r.to_dict()["source_ir"]["emotion"]["sentiment"]
    )
    assert r.target_ir.emotion["sentiment"] == "negative" and "tone_ms" in r.timings


def test_tone_never_raises_and_never_fails_the_check():
    r = Pipeline(None, tone=Fixed(None, None, error=RuntimeError("x"))).check(
        "Pay ₹500.", "₹500 दें।"
    )
    assert r.report.status == "pass" and "tone check failed" in r.tone.notes[0]


def test_pipeline_close_closes_the_tone_checker():
    tone = Fixed(None, None)
    Pipeline(None, tone=tone).close()
    assert tone.closed
