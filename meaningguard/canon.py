"""Canonicalization (spec 02 section 5): rewrite the analysed AST into its canonical form.

C-NUM, C-CUR and C-SLOT are already true by construction (numerics fold every number to one
Decimal and one ISO code; slots come from the parent construct). This module applies C-UNLESS,
C-TIMEREL, C-BOUND (negated bounds), C-RANGE and C-ORDER.
"""

from __future__ import annotations

from . import diagnostics as dg
from .fingerprint import order_key
from .nodes import Bound, Neg, Range, Sentence, TimeRel

_TIME_CANON = {"BY": "ON_OR_BEFORE", "UNTIL": "ON_OR_BEFORE", "SINCE": "ON_OR_AFTER"}
_TIME_NEGATED = {
    "BEFORE": "ON_OR_AFTER",
    "ON_OR_AFTER": "BEFORE",
    "AFTER": "ON_OR_BEFORE",
    "ON_OR_BEFORE": "AFTER",
}
_BOUND_NEGATED = {"GT": "LTE", "GTE": "LT", "LT": "GTE", "LTE": "GT"}


def canonicalize(sentence: Sentence) -> Sentence:
    for clause in sentence.clauses:
        if clause.marker == "UNLESS":  # C-UNLESS: COND(UNLESS, A, B) = COND(IF, NEG(A), B)
            clause.polarity = "POS" if clause.polarity == "NEG" else "NEG"
            clause.flags.add("UNLESS_REWRITTEN")
            sentence.diagnostics.append(dg.make("UNLESS_REWRITTEN", clause.span))
        facts = [_canon_fact(sentence, f) for f in clause.facts]
        clause.facts = sorted(facts, key=order_key)  # C-ORDER
    return sentence


def _canon_fact(sentence, fact):
    if isinstance(fact, TimeRel):
        _canon_timerel(sentence, fact)
    elif (
        isinstance(fact, Neg) and fact.scope == "REL" and isinstance(fact.target, (TimeRel, Bound))
    ):
        return _canon_neg_rel(sentence, fact)
    elif isinstance(fact, Range):
        _canon_range(fact)
    return fact


def _canon_timerel(sentence, fact):
    if fact.rel in ("BY", "UNTIL"):  # C-TIMEREL
        fact.flags.add("BY_UNTIL_AMBIGUOUS")
        sentence.diagnostics.append(dg.make("BY_UNTIL_AMBIGUOUS", fact.span))
    fact.rel = _TIME_CANON.get(fact.rel, fact.rel)


def _canon_neg_rel(sentence, neg):
    inner = neg.target
    if isinstance(inner, TimeRel):
        _canon_timerel(sentence, inner)
        flipped = _TIME_NEGATED.get(inner.rel)
        if flipped is None:  # NEG(ON ...) and NEG(WITHIN ...) stay as negated relations
            return neg
        inner.rel = flipped
    else:
        inner.op = _BOUND_NEGATED[inner.op]  # C-BOUND
    inner.flags |= neg.flags | {"NEG_REWRITTEN"}
    inner.span = neg.span
    sentence.diagnostics.append(dg.make("NEG_REWRITTEN", neg.span))
    return inner


def _canon_range(fact: Range):
    """C-RANGE: only a symmetric source form ('between A and B') may be reordered."""
    lo, hi = getattr(fact.low, "amount", None), getattr(fact.high, "amount", None)
    if (
        fact.source_form == "BETWEEN"
        and lo is not None
        and hi is not None
        and fact.low.type == fact.high.type
        and lo > hi
    ):
        fact.low, fact.high = fact.high, fact.low
