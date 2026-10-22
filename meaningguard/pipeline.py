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

from .cache import CachedTranslator, Tally, open_cache
from .config import Config, load_config
from .ir import IR, compile_text
from .repair import RepairResult, repair
from .tone import ToneChecker, ToneResult, build_tone_checker
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
    tone: ToneResult | None = None  # None when the tone check is off
    cache: dict = field(default_factory=dict)  # {'hits': n, 'misses': n} of the model calls

    @property
    def before(self) -> Report:
        return self.repair.before

    @property
    def report(self) -> Report:
        """The fact check after repair, plus the (advisory) tone findings."""
        after = self.repair.after
        if self.tone is None or not self.tone.findings:
            return after
        findings = after.findings + self.tone.findings
        return Report(findings, after.matched, after.source_tier, after.target_tier)

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
            "tone": self.tone.to_dict() if self.tone else None,
            "cache": self.cache,
            "timings": self.timings,
        }


class Pipeline:
    def __init__(
        self,
        chain: ChainTranslator | None,
        do_repair: bool = True,
        tone: ToneChecker | None = None,
        tone_timeout: float | None = 180,
        cache=None,
    ):
        self.cache = cache
        self.chain = chain
        self.do_repair = do_repair
        self.tone = tone
        self.tone_timeout = tone_timeout

    def check(self, source: str, translation: str | None = None) -> Result:
        """Raises ``EmptyInput`` / ``InputTooLong`` for a bad source and ``TranslationFailed`` when
        no translation was given and the whole chain failed."""
        timings: dict = {}
        tally = Tally(self.cache)
        tally.start()
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
        tone = self._tone(source_ir, outcome, timings)
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
            tone=tone,
            cache=tally.delta(),
        )

    def _tone(self, source_ir: IR, outcome: RepairResult, timings: dict) -> ToneResult | None:
        """Tone of the source and of the final text. Never raises: tone is advisory."""
        if self.tone is None:
            return None
        start = time.perf_counter()
        try:
            tone = call_with_timeout(
                lambda: self.tone.check(source_ir.text, outcome.text), self.tone_timeout
            )
        except Exception as exc:  # noqa: BLE001
            tone = ToneResult(
                None, None, [], [f"tone check failed: {type(exc).__name__}: {exc}"[:160]]
            )
        timings["tone_ms"] = _ms(start)
        source_ir.emotion, outcome.target.emotion = tone.source, tone.target
        return tone

    def close(self) -> None:
        if self.chain is not None:
            self.chain.close()
        if self.tone is not None:
            self.tone.close()


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def build_pipeline(
    cfg: Config | None = None, device: str | None = None, tone: bool = False, cache=None
) -> Pipeline:
    """IndicTrans2 first, then Google if the config allows it. Nothing is loaded until first use.
    ``tone=True`` adds the emotion and sarcasm check (spec 08). Model outputs are cached on disk
    when ``cfg.cache`` is on (``cache`` passes a ready ``Cache`` instead)."""
    from .check_models import resolve_device
    from .translator import GoogleFallback, Translator

    cfg = cfg or load_config()
    cache = cache or open_cache(cfg)
    primary = Translator(
        cfg,
        cfg.models.en_indic,
        cfg.languages.source,
        cfg.languages.target,
        device or resolve_device(cfg.runtime.device),
        revision=cfg.revisions.get(cfg.models.en_indic),
    )
    chain = [primary] + ([GoogleFallback()] if cfg.runtime.google_fallback else [])
    if cache:
        chain = [CachedTranslator(t, cache) for t in chain]
    checker = build_tone_checker(cfg, device, cache) if tone else None
    return Pipeline(ChainTranslator(chain, cfg.runtime.timeout_seconds), tone=checker, cache=cache)
