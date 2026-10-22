"""Typed AST nodes: values and facts (spec 02 sections 1-2, 05 sections 3-4).

Values are immutable and compare by value. Facts are mutable while the parser and the semantic
pass fill in attributes, then ``canon.canonicalize`` fixes them. ``canon_parts()`` returns only what
enters a fingerprint: canonical attributes and child nodes. Surface text, spans and flags never do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

from .numerics import clean


def dec_str(value: Decimal) -> str:
    """Canonical text of a Decimal: ``500000``, ``5E+5`` and ``500000.00`` all give ``500000``."""
    return format(clean(value), "f")


# ---------------------------------------------------------------------------------------------
# Values


@dataclass(frozen=True)
class Value:
    type: ClassVar[str] = "VALUE"

    def fields(self) -> dict:
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {"type": self.type, **self.fields()}

    def canon_parts(self) -> tuple:
        return self.to_dict(), {}


@dataclass(frozen=True)
class Money(Value):
    type: ClassVar[str] = "MONEY"
    amount: Decimal
    currency: str

    def fields(self):
        return {"amount": dec_str(self.amount), "currency": self.currency}


@dataclass(frozen=True)
class Count(Value):
    type: ClassVar[str] = "COUNT"
    amount: Decimal

    def fields(self):
        return {"amount": dec_str(self.amount)}


@dataclass(frozen=True)
class Percent(Value):
    type: ClassVar[str] = "PERCENT"
    amount: Decimal

    def fields(self):
        return {"amount": dec_str(self.amount)}


@dataclass(frozen=True)
class Time(Value):
    type: ClassVar[str] = "TIME"
    hour: int
    minute: int
    period: str | None = None

    def fields(self):
        return {"hour": self.hour, "minute": self.minute, "period": self.period}

    def hour24(self) -> int | None:
        """Hour on a 24-hour clock, or None while the period is unknown for a 1-12 hour."""
        if self.period == "AM":
            return 0 if self.hour == 12 else self.hour
        if self.period == "PM":
            return self.hour if self.hour == 12 else self.hour + 12
        if self.hour == 0 or self.hour > 12:
            return self.hour
        return None


@dataclass(frozen=True)
class Date(Value):
    type: ClassVar[str] = "DATE"
    weekday: int | None = None
    day: int | None = None
    month: int | None = None
    year: int | None = None
    daypart: str | None = None

    def fields(self):
        out = {}
        for name in ("weekday", "day", "month", "year", "daypart"):
            if getattr(self, name) is not None:
                out[name] = getattr(self, name)
        return out


@dataclass(frozen=True)
class Vague(Value):
    type: ClassVar[str] = "VAGUE"
    order: int | None

    def fields(self):
        return {"order": self.order}


@dataclass(frozen=True)
class Duration(Value):
    type: ClassVar[str] = "DURATION"
    amount: Decimal
    unit: str  # SECOND MINUTE HOUR DAY WEEK MONTH YEAR

    def fields(self):
        return {"amount": dec_str(self.amount), "unit": self.unit}


@dataclass(frozen=True)
class Daypart(Value):
    type: ClassVar[str] = "DAYPART"
    part: str  # morning noon evening night

    def fields(self):
        return {"part": self.part}


NUMERIC_TYPES = ("MONEY", "COUNT", "PERCENT")
ANCHOR_TYPES = ("DATE", "TIME", "DAYPART", "DURATION")


# ---------------------------------------------------------------------------------------------
# Facts


@dataclass
class Fact:
    kind: ClassVar[str] = "FACT"
    span: tuple = (0, 0)
    flags: set = field(default_factory=set)

    def children(self) -> dict:
        return {}

    def attrs(self) -> dict:
        return {}

    def canon_parts(self) -> tuple:
        return self.attrs(), {k: v for k, v in self.children().items() if v is not None}


@dataclass
class Quant(Fact):
    kind: ClassVar[str] = "QUANT"
    value: Value = None
    role: str = "AMOUNT"
    approx: bool = False

    def children(self):
        return {"value": self.value}

    def attrs(self):
        return {"role": self.role, "approx": self.approx}


@dataclass
class Change(Fact):
    kind: ClassVar[str] = "CHANGE"
    direction: str = "RISE"
    frm: Value | None = None
    to: Value | None = None
    delta: Value | None = None
    verb: str | None = None  # direction the verb states; ``direction`` may be recomputed (C-DIR)

    def __post_init__(self):
        if self.verb is None:
            self.verb = self.direction

    def children(self):
        return {"from": self.frm, "to": self.to, "delta": self.delta}

    def attrs(self):
        return {"direction": self.direction}


@dataclass
class Range(Fact):
    kind: ClassVar[str] = "RANGE"
    low: Value = None
    high: Value = None
    source_form: str = "FROM_TO"  # BETWEEN, FROM_TO, HYPHEN, TO; not part of the fingerprint

    def children(self):
        return {"low": self.low, "high": self.high}


@dataclass
class Bound(Fact):
    kind: ClassVar[str] = "BOUND"
    op: str = "GT"
    value: Value = None

    def children(self):
        return {"value": self.value}

    def attrs(self):
        return {"op": self.op}


@dataclass
class TimeRel(Fact):
    kind: ClassVar[str] = "TIMEREL"
    rel: str = "ON"
    anchor: Value = None

    def children(self):
        return {"anchor": self.anchor}

    def attrs(self):
        return {"rel": self.rel}


@dataclass
class Neg(Fact):
    kind: ClassVar[str] = "NEG"
    scope: str = "CLAUSE"  # CLAUSE, REL, QUANT
    strength: str = "STRONG"  # STRONG, WEAK
    target: object = None  # Fact for REL, Value or str for QUANT, str (opaque) for CLAUSE

    def children(self):
        if isinstance(self.target, (Fact, Value)):
            return {"target": self.target}
        return {}

    def attrs(self):
        return {"scope": self.scope, "strength": self.strength}


@dataclass
class Literal(Fact):
    """Non-quantity token that must survive translation unchanged: phone, version, ordinal, id."""

    kind: ClassVar[str] = "LITERAL"
    lit: str = "IDENT"  # PHONE, VERSION, ORDINAL, IDENT, NUMBER_ABBREV
    text: str = ""  # canonical text (digits only for PHONE, ORDINAL, NUMBER_ABBREV)

    def attrs(self):
        return {"lit": self.lit, "text": self.text}


KIND_ORDER = ("BOUND", "CHANGE", "LITERAL", "NEG", "QUANT", "RANGE", "TIMEREL")


# ---------------------------------------------------------------------------------------------
# Clauses and the sentence


@dataclass
class Cond:
    op: str  # IF, ELSE
    role: str  # ANTECEDENT, CONSEQUENT
    peer: int | None


@dataclass
class Clause:
    index: int
    sentence: int = 0
    marker: str | None = None  # IF, UNLESS, JABTAK, ELSE: the conditional word that opened it
    span: tuple = (0, 0)
    facts: list = field(default_factory=list)
    polarity: str = "POS"
    pred_text: str | None = None
    cond: Cond | None = None
    flags: set = field(default_factory=set)
    tier2: bool = False  # recovery was needed inside this clause


@dataclass
class Diagnostic:
    code: str
    severity: str  # info, warning, error
    span: tuple | None
    message: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "span": list(self.span) if self.span else None,
            "message": self.message,
        }


@dataclass
class Sentence:
    lang: str
    text: str
    clauses: list
    diagnostics: list
    tier: int = 1
