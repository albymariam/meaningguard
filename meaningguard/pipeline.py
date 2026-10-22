"""Translate, compile both sides, verify, repair: the whole check for one sentence.

``ChainTranslator`` tries the translators in order and moves on when one raises, times out or
returns something that is not Hindi. ``Pipeline.check`` accepts an optional translation, so a user
who already has a Hindi text (or whose translators are all down) can still get the verification.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from .config import Config, load_config
from .ir import IR, compile_text
from .repair import RepairResult, repair
from .verifier import Report

DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class TranslationFailed(RuntimeError):
    """Every translator in the chain failed. ``errors`` lists what each one said."""

    def __init__(self, errors: list):
        super().__init__("no translator produced Hindi: " + "; ".join(errors))
        self.errors = errors


def usable_hindi(text) -> bool:
    return isinstance(text, str) and bool(text.strip()) and bool(DEVANAGARI.search(text))


def call_with_timeout(fn, seconds: float | None):
    """Run ``fn()`` on a daemon thread. A call that never returns is abandoned, not killed."""
    box: dict = {}

    def work():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError(f"no answer after {seconds} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


@dataclass
class ChainTranslator:
    translators: list
    timeout: float | None = 60

    def translate(self, text: str) -> tuple:
        """Returns ``(translation, translator name, notes)``; notes say why earlier ones failed."""
        notes: list = []
        for translator in self.translators:
            name = getattr(translator, "name", type(translator).__name__)
            try:
                out = call_with_timeout(lambda t=translator: t.translate([text]), self.timeout)
            except Exception as exc:  # noqa: BLE001 - any failure moves on to the next translator
                notes.append(f"{name} failed: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            candidate = out[0] if out else None
            if not usable_hindi(candidate):
                notes.append(f"{name} returned no Hindi text")
                continue
            return candidate, name, notes
        raise TranslationFailed(notes)

    def close(self) -> None:
        for translator in self.translators:
            close = getattr(translator, "close", None)
            if close:
                close()


@dataclass
class Result:
    source: str
    translation: str  # what the translator (or the user) gave
    final: str  # the translation after repair; equal to ``translation`` when nothing was edited
    translator: str
    notes: list
    source_ir: IR
    target_ir: IR  # of ``final``
    repair: RepairResult
    timings: dict = field(default_factory=dict)

    @property
    def before(self) -> Report:
        return self.repair.before

    @property
    def report(self) -> Report:
        return self.repair.after

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "translation": self.translation,
            "final": self.final,
            "translator": self.translator,
            "notes": self.notes,
            "status": self.report.status,
            "confidence": self.report.confidence,
            "repair": self.repair.to_dict(),
            "source_ir": self.source_ir.to_dict(),
            "target_ir": self.target_ir.to_dict(),
            "timings": self.timings,
        }


class Pipeline:
    def __init__(self, chain: ChainTranslator | None, do_repair: bool = True):
        self.chain = chain
        self.do_repair = do_repair

    def check(self, source: str, translation: str | None = None) -> Result:
        """Raises ``EmptyInput`` / ``InputTooLong`` for a bad source and ``TranslationFailed`` when
        no translation was given and the whole chain failed."""
        timings: dict = {}
        start = time.perf_counter()
        source_ir = compile_text(source, "en")
        timings["compile_source_ms"] = _ms(start)
        notes: list = []
        if translation is None:
            if self.chain is None:
                raise TranslationFailed(["no translator is configured"])
            start = time.perf_counter()
            translation, translator, notes = self.chain.translate(source_ir.text)
            timings["translate_ms"] = _ms(start)
        else:
            translator = "provided"
        start = time.perf_counter()
        outcome = repair(source_ir, translation, apply=self.do_repair)
        timings["verify_repair_ms"] = _ms(start)
        return Result(
            source=source_ir.text,
            translation=translation,
            final=outcome.text,
            translator=translator,
            notes=notes,
            source_ir=source_ir,
            target_ir=outcome.target,
            repair=outcome,
            timings=timings,
        )

    def close(self) -> None:
        if self.chain is not None:
            self.chain.close()


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def build_pipeline(cfg: Config | None = None, device: str | None = None) -> Pipeline:
    """IndicTrans2 first, then Google if the config allows it. Nothing is loaded until first use."""
    from .check_models import resolve_device
    from .translator import GoogleFallback, Translator

    cfg = cfg or load_config()
    primary = Translator(
        cfg,
        cfg.models.en_indic,
        cfg.languages.source,
        cfg.languages.target,
        device or resolve_device(cfg.runtime.device),
        revision=cfg.revisions.get(cfg.models.en_indic),
    )
    chain = [primary] + ([GoogleFallback()] if cfg.runtime.google_fallback else [])
    return Pipeline(ChainTranslator(chain, cfg.runtime.timeout_seconds))
