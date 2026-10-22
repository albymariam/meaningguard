"""The Streamlit app, run headless with fake translators and a fake tone checker."""

from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from meaningguard import pipeline as mg  # noqa: E402
from meaningguard.cache import Cache, CachedTranslator  # noqa: E402
from meaningguard.tone import ToneResult  # noqa: E402
from meaningguard.verifier import compare_tone  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")


class Translator:
    name = "fake"

    def __init__(self, answer="₹५०० दें।", error=None):
        self.answer, self.error = answer, error

    cache_key = ("fake",)

    def translate(self, sentences):
        if self.error:
            raise self.error
        return [self.answer for _ in sentences]


def record(sentiment, sarcasm=False):
    signal = {"name": "sentiment:xlmr", "label": sentiment, "prob": 0.9, "sentiment": sentiment}
    return {
        "sentiment": sentiment,
        "label": "anger",
        "confidence": 0.8,
        "possible_sarcasm": sarcasm,
        "signals": [signal],
    }


class Tone:
    def __init__(self, src, tgt):
        self.src, self.tgt = src, tgt

    def check(self, source, target):
        return ToneResult(self.src, self.tgt, compare_tone(self.src, self.tgt), ["a note"])

    def close(self):
        pass


class Holder(dict):
    """What the fake pipeline is built from; changing it drops the pipeline the app cached."""

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        st.cache_resource.clear()


@pytest.fixture
def app(monkeypatch):
    holder = Holder(translator=Translator(), tone=Tone(record("negative"), record("negative")))

    def build(cfg=None, device=None, tone=False):
        cache = holder.get("cache")
        translator = holder["translator"]
        if cache:
            translator = CachedTranslator(translator, cache)
        chain = mg.ChainTranslator([translator], 5)
        return mg.Pipeline(chain, tone=holder["tone"] if tone else None, cache=cache)

    monkeypatch.setattr(mg, "build_pipeline", build)
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=30)
    at.holder = holder
    return at.run()


def press(at, label):
    next(b for b in at.button if b.label == label).click()
    return at.run()


def texts(elements):
    return " ".join(e.value for e in elements)


def own_translation(at, english, hindi):
    at.text_area(key="source").set_value(english)
    at.checkbox(key="own").check()
    at.run()
    at.text_area(key="translation").set_value(hindi)
    return press(at, "Check")


def test_first_page_has_no_error(app):
    assert not app.exception and "MeaningGuard-Indic" in texts(app.markdown)
    assert [b.label for b in app.button][:2] == ["Wrong number", "Wrong currency"]


def test_wrong_number_is_repaired_and_shown(app):
    at = own_translation(app, "Pay ₹500 by Friday.", "शुक्रवार तक ₹५५० का भुगतान करें।")
    assert not at.exception
    html = texts(at.markdown)
    assert "Meaning preserved" in html and "mg-status-pass" in html
    assert "1 wrong value fixed automatically" in html
    assert 'class="mg-ok"' in html and 'class="mg-error"' in html  # after and before
    assert "५००" in html
    assert any(sub.value == "Repair" for sub in at.subheader)


def test_dropped_negation_fails_and_is_listed(app):
    at = own_translation(app, "Do not pay after Friday.", "शुक्रवार के बाद भुगतान करें।")
    body = texts(at.markdown)
    assert "Meaning may have changed" in body and "mg-status-fail" in body
    assert "error[NEGATION_DROPPED]" in body
    assert not any(sub.value == "Repair" for sub in at.subheader)


def test_translating_live_uses_the_chain_and_shows_tone(app):
    app.holder["translator"] = Translator("₹५०० दें।")
    app.text_area(key="source").set_value("Pay ₹500.")
    at = press(app, "Check")
    body = texts(at.markdown)
    assert "Meaning preserved" in body
    assert "mg-s-negative" in body and "confidence 80%" in body
    assert "a note" in texts(at.caption)


def test_tone_warning_changes_the_verdict_and_toggle_turns_tone_off(app):
    app.holder["tone"] = Tone(record("positive"), record("negative"))
    app.text_area(key="source").set_value("Pay ₹500.")
    at = press(app, "Check")
    body = texts(at.markdown)
    assert "Check these points" in body and "mg-status-warn" in body
    assert "warning[TONE_POLARITY_FLIPPED]" in body
    next(t for t in at.toggle if t.label.startswith("Tone")).set_value(False)
    at = press(at, "Check")
    assert "Meaning preserved" in texts(at.markdown) and "tone check is off" in texts(at.caption)


def test_sarcasm_is_shown_for_the_source(app):
    app.holder["tone"] = Tone(record("uncertain", sarcasm=True), record("positive"))
    app.text_area(key="source").set_value("Oh great.")
    at = press(app, "Check")
    body = texts(at.markdown)
    assert "possible sarcasm" in body and "warning[SARCASM_POSSIBLY_LOST]" in body


def test_translation_failure_suggests_pasting(app):
    app.holder["translator"] = Translator(error=RuntimeError("down"))
    app.text_area(key="source").set_value("Pay ₹500.")
    at = press(app, "Check")
    assert "No translator produced Hindi" in texts(at.error) and "Paste your own" in texts(at.error)


def test_bad_input_is_a_warning_not_a_crash(app):
    at = press(app, "Check")  # empty source
    assert not at.exception and "Cannot read the input" in texts(at.warning)
    at.text_area(key="source").set_value("Pay ₹500.")
    at.checkbox(key="own").check()
    at.run()
    at = press(at, "Check")
    assert "Paste the Hindi translation" in texts(at.warning)


def test_examples_fill_the_form(app):
    at = press(app, "Wrong number")
    assert at.text_area(key="source").value == "Pay ₹500 by Friday."
    assert at.checkbox(key="own").value and at.text_area(key="translation").value.startswith(
        "शुक्रवार"
    )
    at = press(at, "Translate it")
    assert not at.checkbox(key="own").value and "5,000" in at.text_area(key="source").value


def test_cache_indicator_and_clear_button(app):
    cache = Cache()
    app.holder["cache"] = cache
    at = app.run()
    assert "0 saved model results" in texts(at.caption)
    at.text_area(key="source").set_value("Pay ₹500.")
    at = press(at, "Check")
    assert "Answered from the cache" not in texts(at.caption)
    at = press(at, "Check")
    assert "Answered from the cache" in texts(at.caption)
    assert "1 saved model results" in texts(at.caption) or "2 saved" in texts(at.caption)
    at = press(at, "Clear cache")
    assert cache.stats()["total"] == 0


def test_cache_off_is_said_in_the_sidebar(app):
    assert "The cache is off." in texts(app.caption)
