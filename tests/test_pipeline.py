"""Translator chain, timeouts and the check pipeline (no models, no network)."""

import json
import sys
import time
from types import SimpleNamespace

import pytest

from meaningguard.config import load_config
from meaningguard.ir import compile_text
from meaningguard.lexer import EmptyInput
from meaningguard.pipeline import (
    ChainTranslator,
    Pipeline,
    TranslationFailed,
    build_pipeline,
    call_with_timeout,
    usable_hindi,
)
from meaningguard.translator import GoogleFallback


class Fake:
    def __init__(self, name, answer=None, error=None, delay=0.0):
        self.name, self.answer, self.error, self.delay = name, answer, error, delay
        self.calls = 0
        self.closed = False

    def translate(self, sentences):
        self.calls += 1
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return [self.answer for _ in sentences]

    def close(self):
        self.closed = True


def test_usable_hindi():
    assert usable_hindi("कीमत ₹500 है।")
    assert not usable_hindi("The price is 500")
    assert not usable_hindi("   ") and not usable_hindi(None)


def test_timeout_helper():
    assert call_with_timeout(lambda: 7, 1) == 7
    with pytest.raises(TimeoutError):
        call_with_timeout(lambda: time.sleep(5), 0.05)
    with pytest.raises(ValueError):
        call_with_timeout(lambda: (_ for _ in ()).throw(ValueError("x")), 1)


def test_first_translator_wins():
    a, b = Fake("a", "₹५०० दें।"), Fake("b", "₹६०० दें।")
    text, name, notes = ChainTranslator([a, b]).translate("Pay ₹500.")
    assert (text, name, notes, b.calls) == ("₹५०० दें।", "a", [], 0)


@pytest.mark.parametrize(
    "first",
    [
        Fake("a", error=RuntimeError("model did not load")),
        Fake("a", answer="Pay 500"),  # not Hindi
        Fake("a", answer=""),
        Fake("a", answer="₹५०० दें।", delay=1.0),  # too slow
    ],
)
def test_chain_falls_back_and_says_why(first):
    second = Fake("b", "₹५०० दें।")
    text, name, notes = ChainTranslator([first, second], timeout=0.2).translate("Pay ₹500.")
    assert name == "b" and text == "₹५०० दें।" and len(notes) == 1 and notes[0].startswith("a ")


def test_all_failing_raises_with_every_reason():
    chain = ChainTranslator([Fake("a", error=OSError("down")), Fake("b", answer="x")], timeout=1)
    with pytest.raises(TranslationFailed) as err:
        chain.translate("Pay ₹500.")
    assert len(err.value.errors) == 2 and "OSError" in err.value.errors[0]


def test_close_reaches_every_translator():
    a, b = Fake("a"), Fake("b")
    ChainTranslator([a, b]).close()
    assert a.closed and b.closed


def make(answer, **kwargs):
    return Pipeline(ChainTranslator([Fake("fake", answer)], timeout=2), **kwargs)


def test_check_with_a_correct_translation():
    result = make("₹५०० दें।").check("Pay ₹500.")
    assert result.report.status == "pass" and result.final == result.translation
    assert result.translator == "fake" and not result.repair.repaired


def test_check_repairs_a_wrong_number():
    result = make("₹५५० दें।").check("Pay ₹500.")
    assert result.translation == "₹५५० दें।" and result.final == "₹५०० दें।"
    assert result.before.status == "fail" and result.report.status == "pass"
    assert result.target_ir.text == result.final


def test_check_reports_what_it_cannot_repair():
    result = make("शुक्रवार के बाद भुगतान करें।").check("Do not pay after Friday.")
    assert result.report.codes() == ["NEGATION_DROPPED"] and result.final == result.translation


def test_repair_can_be_switched_off():
    result = make("₹५५० दें।", do_repair=False).check("Pay ₹500.")
    assert result.final == result.translation and result.report.codes() == ["NUMBER_CHANGED"]


def test_a_given_translation_skips_the_translators():
    fake = Fake("fake", "₹५०० दें।")
    result = Pipeline(ChainTranslator([fake])).check("Pay ₹500.", "₹६०० दें।")
    assert fake.calls == 0 and result.translator == "provided" and result.final == "₹५०० दें।"


def test_no_translators_and_no_translation():
    with pytest.raises(TranslationFailed):
        Pipeline(None).check("Pay ₹500.")
    assert Pipeline(None).check("Pay ₹500.", "₹५०० दें।").report.status == "pass"


def test_bad_source_is_rejected_before_any_translation():
    fake = Fake("fake", "₹५०० दें।")
    with pytest.raises(EmptyInput):
        Pipeline(ChainTranslator([fake])).check("   ")
    assert fake.calls == 0


def test_result_to_dict_is_json_and_complete():
    result = make("₹५५० दें।").check("Pay ₹500.")
    data = json.loads(json.dumps(result.to_dict()))
    assert data["status"] == "pass" and data["repair"]["edits"][0]["new"] == "५००"
    assert data["source_ir"]["schema"] == "mgir/1" and "translate_ms" in data["timings"]


def test_fallback_note_is_in_the_result():
    chain = ChainTranslator([Fake("a", error=RuntimeError("x")), Fake("b", "₹५०० दें।")])
    result = Pipeline(chain).check("Pay ₹500.")
    assert result.translator == "b" and "a failed" in result.notes[0]


# -- the real translators, with fakes for the libraries --------------------------------------


def fake_deep_translator(monkeypatch, outcomes):
    calls = []

    class FakeGoogle:
        def __init__(self, source, target):
            calls.append((source, target))

        def translate(self, text):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setitem(
        sys.modules, "deep_translator", SimpleNamespace(GoogleTranslator=FakeGoogle)
    )
    return calls


def test_google_fallback_retries_then_succeeds(monkeypatch):
    calls = fake_deep_translator(monkeypatch, [ConnectionError("429"), "₹५०० दें।"])
    out = GoogleFallback(first_wait=0).translate(["Pay ₹500."])
    assert out == ["₹५०० दें।"] and calls == [("en", "hi")]


def test_google_fallback_gives_up(monkeypatch):
    fake_deep_translator(monkeypatch, [ConnectionError("x")] * 3)
    with pytest.raises(RuntimeError, match="Google translation failed"):
        GoogleFallback(first_wait=0).translate(["Pay ₹500."])


def test_build_pipeline_wires_the_chain_from_config(monkeypatch):
    cfg = load_config()
    names = [t.name for t in build_pipeline(cfg, device="cpu").chain.translators]
    assert names == [cfg.models.en_indic, "google (deep-translator)"]
    off = SimpleNamespace(
        models=cfg.models,
        runtime=SimpleNamespace(**{**cfg.runtime.__dict__, "google_fallback": False}),
        languages=cfg.languages,
        revisions={cfg.models.en_indic: "abc123"},
    )
    chain = build_pipeline(off, device="cpu").chain
    assert [t.name for t in chain.translators] == [cfg.models.en_indic]
    assert chain.translators[0].revision == "abc123"


def test_pipeline_output_matches_a_direct_compile():
    result = make("₹५०० दें।").check("Pay ₹500.")
    assert result.source_ir.fp == compile_text("Pay ₹500.", "en").fp
