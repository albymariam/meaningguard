"""Typed, validated loader for config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
DEVICES = {"auto", "cpu", "cuda"}


class ConfigError(ValueError):
    """Raised when config.yaml is missing, malformed or has invalid values."""


@dataclass(frozen=True)
class ModelIds:
    en_indic: str
    indic_en: str
    sentiment: str
    emotion_en: str
    irony_en: str


@dataclass(frozen=True)
class Runtime:
    device: str
    num_beams: int
    max_length: int
    use_cache: bool = True
    timeout_seconds: int = 60  # per translator in the fallback chain
    google_fallback: bool = True  # deep-translator, no key; used only when IndicTrans2 fails


@dataclass(frozen=True)
class Languages:
    source: str
    target: str


@dataclass(frozen=True)
class EmotionSettings:
    """Thresholds of the voting rule (spec 08). Tuned on dev labels only."""

    min_prob: float = 0.4  # a signal below this abstains
    label_prob: float = 0.5  # the emotion label needs at least this
    irony_prob: float = 0.9  # sarcasm on the irony score alone (tuned on dev, spec 08)
    irony_low: float = 0.35  # sarcasm when the surface sentiment contradicts the emotion
    max_length: int = 128  # tokens read by each classifier


@dataclass(frozen=True)
class Config:
    models: ModelIds
    runtime: Runtime
    languages: Languages
    revisions: dict = field(default_factory=dict)  # model id -> pinned commit (tools/pin_models.py)
    emotion: EmotionSettings = field(default_factory=EmotionSettings)


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"missing or invalid section: {key}")
    return value


def _text(section: dict[str, Any], key: str, where: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def _positive_int(section: dict[str, Any], key: str, where: str) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(f"{where}.{key} must be a positive integer")
    return value


def _boolean(section: dict[str, Any], key: str, where: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.{key} must be true or false")
    return value


def _revisions(raw: dict[str, Any]) -> dict[str, str]:
    value = raw.get("revisions") or {}
    if not isinstance(value, dict):
        raise ConfigError("revisions must be a mapping of model id to commit")
    for model, commit in value.items():
        if not isinstance(model, str) or not isinstance(commit, str) or not commit.strip():
            raise ConfigError(f"revisions.{model} must be a non-empty string")
    return {m: c.strip() for m, c in value.items()}


def _emotion(raw: dict[str, Any]) -> EmotionSettings:
    section = raw.get("emotion") or {}
    if not isinstance(section, dict):
        raise ConfigError("emotion must be a mapping")
    base = EmotionSettings()
    values = {}
    for key in ("min_prob", "label_prob", "irony_prob", "irony_low"):
        value = section.get(key, getattr(base, key))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ConfigError(f"emotion.{key} must be a number from 0 to 1")
        values[key] = float(value)
    values["max_length"] = (
        _positive_int(section, "max_length", "emotion")
        if "max_length" in section
        else base.max_length
    )
    return EmotionSettings(**values)


def load_config(path: Path | str | None = None) -> Config:
    """Load and validate the configuration file."""
    target = Path(path) if path is not None else DEFAULT_PATH
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {target}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config file must contain a mapping")

    models = _section(raw, "models")
    runtime = _section(raw, "runtime")
    languages = _section(raw, "languages")

    device = _text(runtime, "device", "runtime")
    if device not in DEVICES:
        raise ConfigError(f"runtime.device must be one of {sorted(DEVICES)}")

    return Config(
        models=ModelIds(
            en_indic=_text(models, "en_indic", "models"),
            indic_en=_text(models, "indic_en", "models"),
            sentiment=_text(models, "sentiment", "models"),
            emotion_en=_text(models, "emotion_en", "models"),
            irony_en=_text(models, "irony_en", "models"),
        ),
        runtime=Runtime(
            device=device,
            num_beams=_positive_int(runtime, "num_beams", "runtime"),
            max_length=_positive_int(runtime, "max_length", "runtime"),
            use_cache=_boolean(runtime, "use_cache", "runtime", default=True),
            timeout_seconds=(
                _positive_int(runtime, "timeout_seconds", "runtime")
                if "timeout_seconds" in runtime
                else 60
            ),
            google_fallback=_boolean(runtime, "google_fallback", "runtime", default=True),
        ),
        languages=Languages(
            source=_text(languages, "source", "languages"),
            target=_text(languages, "target", "languages"),
        ),
        revisions=_revisions(raw),
        emotion=_emotion(raw),
    )
