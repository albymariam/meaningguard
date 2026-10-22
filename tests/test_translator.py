"""Translator wrapper tests with a fake model (no downloads, no torch)."""

from types import SimpleNamespace

from meaningguard.config import load_config
from meaningguard.translator import Translator


class FakeEncoded(dict):
    def to(self, device):
        return self


class FakeTokenizer:
    def __init__(self, batches):
        self.batches = batches

    def __call__(self, batch, **kwargs):
        self.batches.append(list(batch))
        return FakeEncoded(batch=batch)

    def batch_decode(self, generated, **kwargs):
        return [f"hi:{item}" for item in generated]


def make(monkeypatch, batch_size=2):
    state = {"loads": 0, "batches": []}
    processor = SimpleNamespace(
        preprocess_batch=lambda texts, src_lang, tgt_lang: [
            f"{src_lang}>{tgt_lang} {t}" for t in texts
        ],
        postprocess_batch=lambda texts, lang: [t.upper() for t in texts],
    )
    tokenizer = FakeTokenizer(state["batches"])
    translator = Translator(load_config(), "model", "eng_Latn", "hin_Deva", "cpu", batch_size)

    def fake_load():
        state["loads"] += 1
        return processor, tokenizer, object()

    monkeypatch.setattr(translator, "_load", fake_load)
    monkeypatch.setattr(translator, "_generate", lambda model, encoded: encoded["batch"])
    return translator, state


def test_loads_once_and_translates_in_batches(monkeypatch):
    translator, state = make(monkeypatch)
    out = translator.translate(["a", "b", "c"])
    assert out == ["HI:ENG_LATN>HIN_DEVA A", "HI:ENG_LATN>HIN_DEVA B", "HI:ENG_LATN>HIN_DEVA C"]
    assert [len(b) for b in state["batches"]] == [2, 1]
    translator.translate(["d"])
    assert state["loads"] == 1


def test_empty_input_does_not_load(monkeypatch):
    translator, state = make(monkeypatch)
    assert translator.translate([]) == []
    assert state["loads"] == 0


def test_close_forgets_the_model(monkeypatch):
    translator, state = make(monkeypatch)
    translator.translate(["a"])
    translator.close()
    translator.translate(["b"])
    assert state["loads"] == 2
