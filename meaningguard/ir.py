"""MG-IR: the serialisable result of the front end (spec 05).

``compile_text(text, lang)`` runs lexer, numeric assembly, parser, semantic analysis,
canonicalization and fingerprinting, and returns an ``IR``. The source and the translation use the
same schema, so the verifier compares two ``IR`` objects fact by fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from . import diagnostics as dg
from .canon import canonicalize
from .fingerprint import clause_fp, fp, sentence_fp
from .nodes import (
    Bound,
    Change,
    Clause,
    Fact,
    Literal,
    Neg,
    Quant,
    Range,
    Sentence,
    TimeRel,
)
from .parser import parse
from .semantics import analyze

SCHEMA = "mgir/1"


class IRFormatError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------
# Serialisation of facts


def _value(value):
    return value.to_dict() if value is not None else None


def fact_to_dict(fact: Fact) -> dict:
    out: dict = {"kind": fact.kind}
    if isinstance(fact, Quant):
        out.update(value=_value(fact.value), role=fact.role, approx=fact.approx)
    elif isinstance(fact, Change):
        out.update(
            direction=fact.direction,
            verb=fact.verb,
            **{"from": _value(fact.frm)},
            to=_value(fact.to),
            delta=_value(fact.delta),
        )
    elif isinstance(fact, Range):
        out.update(low=_value(fact.low), high=_value(fact.high))
    elif isinstance(fact, Bound):
        out.update(op=fact.op, value=_value(fact.value))
    elif isinstance(fact, TimeRel):
        out.update(rel=fact.rel, anchor=_value(fact.anchor))
    elif isinstance(fact, Neg):
        target = fact.target
        if isinstance(target, Fact):
            target = fact_to_dict(target)
        elif target is not None and not isinstance(target, str):
            target = target.to_dict()
        out.update(scope=fact.scope, strength=fact.strength, target=target)
    elif isinstance(fact, Literal):
        out.update(lit=fact.lit, text=fact.text)
    out["fp"] = fp(fact)
    out["span"] = list(fact.span)
    out["flags"] = sorted(fact.flags)
    return out


def clause_to_dict(clause: Clause) -> dict:
    cond = None
    if clause.cond is not None:
        cond = {"op": clause.cond.op, "role": clause.cond.role, "peer": clause.cond.peer}
    return {
        "index": clause.index,
        "polarity": clause.polarity,
        "pred_text": clause.pred_text,
        "facts": [fact_to_dict(f) for f in clause.facts],
        "cond": cond,
        "flags": sorted(clause.flags),
        "fp": clause_fp(clause),
    }


# ---------------------------------------------------------------------------------------------
# IR


@dataclass
class IR:
    lang: str
    text: str
    tier: int
    clauses: list
    diagnostics: list
    emotion: dict | None = None
    fp: str = ""
    schema: str = SCHEMA

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "lang": self.lang,
            "text": self.text,
            "tier": self.tier,
            "fp": self.fp,
            "clauses": self.clauses,
            "emotion": self.emotion,
            "diagnostics": self.diagnostics,
        }

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> IR:
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise IRFormatError(f"expected schema {SCHEMA!r}")
        for key in ("lang", "text", "tier", "clauses", "diagnostics"):
            if key not in data:
                raise IRFormatError(f"missing field {key!r}")
        if data["lang"] not in ("en", "hi") or data["tier"] not in (1, 2, 3):
            raise IRFormatError("bad lang or tier")
        return cls(
            lang=data["lang"],
            text=data["text"],
            tier=data["tier"],
            clauses=data["clauses"],
            diagnostics=data["diagnostics"],
            emotion=data.get("emotion"),
            fp=data.get("fp", ""),
        )

    @classmethod
    def from_json(cls, text: str) -> IR:
        return cls.from_dict(json.loads(text))

    # -- convenience for the verifier and the tests ---------------------------------------------
    def facts(self) -> list:
        return [f for clause in self.clauses for f in clause["facts"]]

    def codes(self) -> set:
        """Every diagnostic code and every fact or clause flag."""
        out = {d["code"] for d in self.diagnostics}
        for clause in self.clauses:
            out |= set(clause.get("flags", []))
            for fact in clause["facts"]:
                out |= set(fact["flags"])
        return out


# ---------------------------------------------------------------------------------------------
# Compilation


def compile_text(text: str, lang: str, emotion: dict | None = None) -> IR:
    """Front end for one input. Raises ``EmptyInput`` or ``InputTooLong`` from the lexer."""
    sentence, res, nres = parse(text, lang)
    analyze(sentence)
    canonicalize(sentence)
    diagnostics = _collect_diagnostics(sentence, res, nres)
    clauses = [clause_to_dict(c) for c in sentence.clauses]
    return IR(
        lang=lang,
        text=sentence.text,
        tier=_tier(sentence, nres),
        clauses=clauses,
        diagnostics=diagnostics,
        emotion=emotion,
        fp=sentence_fp([c["fp"] for c in clauses]),
    )


def _tier(sentence: Sentence, nres) -> int:
    with_facts = [c for c in sentence.clauses if c.facts]
    recognised = with_facts or any(c.cond or c.polarity == "NEG" for c in sentence.clauses)
    if not recognised or (
        with_facts and all("INTERNAL_PARSE_ERROR" in c.flags for c in with_facts)
    ):
        return 3
    if nres.errors or any(c.tier2 for c in sentence.clauses):
        return 2
    return 1


# Parser decisions recorded as fact flags; each also becomes an info diagnostic at the fact span.
_FACT_FLAG_DIAGNOSTICS = (
    "IMPLICIT_ON",
    "TAK_AS_UPTO",
    "SCALE_SHARED",
    "PERIOD_SHARED",
    "PERIOD_FROM_DAYPART",
    "DATE_ORDER_ASSUMED",
    "APPROX_IN_COMPOUND",
)


def _collect_diagnostics(sentence: Sentence, res, nres) -> list:
    diags = list(sentence.diagnostics)
    for clause in sentence.clauses:
        for fact in clause.facts:
            for flag in sorted(fact.flags.intersection(_FACT_FLAG_DIAGNOSTICS)):
                diags.append(dg.make(flag, fact.span))
    for err in nres.errors:
        diags.append(dg.make(err.code, (res.tokens[err.start].start, res.tokens[err.end - 1].end)))
    for tok in res.tokens:
        for flag in sorted(tok.flags):
            if flag in dg.CODES and flag != "NON_QUANTITY":
                diags.append(dg.make(flag, (tok.start, tok.end)))
        if (
            "NON_QUANTITY" in tok.flags
            and tok.attrs.get("opaque")
            and any(ch.isdigit() for ch in tok.text)
        ):
            diags.append(dg.make("NON_QUANTITY", (tok.start, tok.end)))
    for flag in sorted(res.flags & {"MIXED_SCRIPT"}):
        diags.append(dg.make(flag))
    seen, out = set(), []
    for d in diags:
        key = (d.code, d.span)
        if key not in seen:
            seen.add(key)
            out.append(d.to_dict())
    out.sort(key=lambda d: (d["span"] is None, d["span"] or [0, 0], d["code"]))
    return out
