"""MG-IR: structured representation of facts and emotion for one sentence."""
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Quantity:
    raw: str                      # text as written, e.g. "₹5 लाख"
    value: float                  # normalized, e.g. 500000.0
    currency: Optional[str] = None  # "INR" or None
    pos: int = 0                  # token index


@dataclass
class Negation:
    raw: str
    pos: int = 0


@dataclass
class Emotion:
    sentiment: str = "neutral"    # positive / negative / neutral
    label: str = "neutral"        # joy / anger / sadness / fear / neutral / uncertain
    possible_sarcasm: bool = False
    confidence: float = 0.0


@dataclass
class MGIR:
    lang: str = "en"
    text: str = ""
    quantities: list = field(default_factory=list)
    negations: list = field(default_factory=list)
    conditions: list = field(default_factory=list)
    comparisons: list = field(default_factory=list)
    emotion: Emotion = field(default_factory=Emotion)

    def to_dict(self):
        return asdict(self)
