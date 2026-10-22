"""Tone check (spec 08): emotion of the source and of the translation, with graceful degradation.

The Hindi side is read directly (sentiment model) and through an English back-translation (all
emotion models). Whatever fails is left out and named in ``notes``; a check without tone data
simply has no tone findings.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config, load_config
from .emotion import EmotionAnalyzer, build_analyzer
from .verifier import Finding, compare_tone


@dataclass
class ToneResult:
    source: dict | None
    target: dict | None
    findings: list  # Finding
    notes: list

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "findings": [f.to_dict() for f in self.findings],
            "notes": self.notes,
        }


def _why(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:120]}"


@dataclass
class ToneChecker:
    analyzer: EmotionAnalyzer
    back: object | None = None  # translator hi -> en, ``translate([text]) -> [text]``

    def check(self, source: str, target: str) -> ToneResult:
        notes: list = []
        try:
            src = self.analyzer.analyze_source(source)
        except Exception as exc:  # noqa: BLE001 - tone is advisory, it must never break a check
            return ToneResult(None, None, [], [f"source tone unavailable ({_why(exc)})"])
        english = None
        if self.back is not None:
            try:
                out = self.back.translate([target])
                english = out[0] if out and isinstance(out[0], str) and out[0].strip() else None
                if english is None:
                    notes.append("back-translation returned no text")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"back-translation failed ({_why(exc)})")
        try:
            tgt = self.analyzer.analyze_target(target, english)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"target tone unavailable ({_why(exc)})")
            return ToneResult(src, None, [], notes)
        if english is None:
            notes.append("target tone read from the Hindi text only")
        findings: list[Finding] = compare_tone(src, tgt)
        return ToneResult(src, tgt, findings, notes)

    def close(self) -> None:
        self.analyzer.close()
        close = getattr(self.back, "close", None)
        if close:
            close()


def build_tone_checker(cfg: Config | None = None, device: str | None = None) -> ToneChecker:
    from .check_models import resolve_device
    from .translator import Translator

    cfg = cfg or load_config()
    device = device or resolve_device(cfg.runtime.device)
    back = Translator(
        cfg,
        cfg.models.indic_en,
        cfg.languages.target,
        cfg.languages.source,
        device,
        revision=cfg.revisions.get(cfg.models.indic_en),
    )
    return ToneChecker(build_analyzer(cfg, device), back)
