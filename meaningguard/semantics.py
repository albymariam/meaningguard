"""Semantic analysis: attribute evaluation on the raw AST (spec 02).

Synthesized attributes: clause polarity (from NEG facts), change direction (from the numbers).
Inherited attributes: the currency, percent sign or duration unit that a partner number lends to a
bare number inside one construct (C-CUR-RANGE), and the slot role of a quantity (C-SLOT).
Conditional words are resolved to ``Cond`` links between clauses.
"""

from __future__ import annotations

from . import diagnostics as dg
from .nodes import (
    Change,
    Cond,
    Duration,
    Money,
    Neg,
    Percent,
    Range,
    Sentence,
    Time,
    TimeRel,
)


def unify(a, b) -> tuple:
    """C-CUR-RANGE and its siblings. Returns ``(a, b, flags)`` for two ends of one construct."""
    flags: set = set()
    if a.type == b.type:
        if a.type == "MONEY" and a.currency != b.currency:
            flags.add("CURRENCY_MISMATCH_IN_RANGE")
        return a, b, flags
    if a.type == "COUNT" and b.type in ("MONEY", "PERCENT", "DURATION"):
        return _lend(a, b), b, {_lend_flag(b)}
    if b.type == "COUNT" and a.type in ("MONEY", "PERCENT", "DURATION"):
        return a, _lend(b, a), {_lend_flag(a)}
    return a, b, flags


def _lend(count, donor):
    if donor.type == "MONEY":
        return Money(count.amount, donor.currency)
    if donor.type == "PERCENT":
        return Percent(count.amount)
    return Duration(count.amount, donor.unit)


def _lend_flag(donor) -> str:
    return {"MONEY": "CURRENCY_INHERITED", "PERCENT": "PERCENT_INHERITED"}.get(
        donor.type, "UNIT_INHERITED"
    )


def _amount(value):
    return getattr(value, "amount", None)


def analyze(sentence: Sentence) -> Sentence:
    for clause in sentence.clauses:
        for fact in clause.facts:
            _analyze_fact(sentence, clause, fact)
        _polarity(sentence, clause)
    _conditions(sentence)
    return sentence


def _note(sentence, fact, code):
    fact.flags.add(code)
    sentence.diagnostics.append(dg.make(code, fact.span))


def _analyze_fact(sentence, clause, fact):
    if isinstance(fact, Range):
        fact.low, fact.high, flags = unify(fact.low, fact.high)
        for code in flags:
            _note(sentence, fact, code)
        lo, hi = _amount(fact.low), _amount(fact.high)
        if lo is not None and hi is not None and fact.low.type == fact.high.type and lo > hi:
            _note(sentence, fact, "RANGE_REVERSED")
    elif isinstance(fact, Change):
        if fact.frm is not None and fact.to is not None:
            fact.frm, fact.to, flags = unify(fact.frm, fact.to)
            for code in flags:
                _note(sentence, fact, code)
            _direction(sentence, fact)
    for value in _time_values(fact):
        if value.period is None and 1 <= value.hour <= 12:
            _note(sentence, fact, "TIME_PERIOD_MISSING")
            break


def _time_values(fact):
    if isinstance(fact, TimeRel) and isinstance(fact.anchor, Time):
        return [fact.anchor]
    if isinstance(fact, Range):
        return [v for v in (fact.low, fact.high) if isinstance(v, Time)]
    return []


def _direction(sentence, fact):
    """C-DIR: the sign of ``to - from`` decides; the verb is only a consistency check."""
    a, b = fact.frm, fact.to
    if a.type != b.type or _amount(a) is None:
        return
    if a.type == "MONEY" and a.currency != b.currency:
        return
    if b.amount > a.amount:
        computed = "RISE"
    elif b.amount < a.amount:
        computed = "FALL"
    else:
        _note(sentence, fact, "CHANGE_NO_DELTA")
        return
    if computed != fact.direction:
        _note(sentence, fact, "SOURCE_INCONSISTENT")
        fact.direction = computed


def _polarity(sentence, clause):
    if clause.marker == "JABTAK":
        negs = [f for f in clause.facts if isinstance(f, Neg) and f.scope == "CLAUSE"]
        if negs:  # 'जब तक A नहीं' : the negation belongs to the construct and is consumed
            clause.facts.remove(negs[0])
            clause.marker = "UNLESS"
            sentence.diagnostics.append(dg.make("UNTIL_AS_UNLESS", clause.span))
        else:
            clause.marker = "IF"
            sentence.diagnostics.append(dg.make("UNTIL_AS_IF", clause.span))
    count = sum(1 for f in clause.facts if isinstance(f, Neg) and f.scope == "CLAUSE")
    clause.polarity = "NEG" if count % 2 else "POS"
    if count >= 2:
        clause.flags.add("DOUBLE_NEG")
        sentence.diagnostics.append(dg.make("DOUBLE_NEG", clause.span))


def _conditions(sentence: Sentence):
    by_sentence: dict = {}
    for clause in sentence.clauses:
        by_sentence.setdefault(clause.sentence, []).append(clause)
    if len(by_sentence) > 1:
        sentence.diagnostics.append(dg.make("MULTI_SENTENCE"))
    for group in by_sentence.values():
        for pos, clause in enumerate(group):
            if clause.marker in ("IF", "UNLESS"):
                _link_condition(sentence, group, pos)
        for pos, clause in enumerate(group):
            if clause.marker == "ELSE":
                if pos == 0:
                    clause.tier2 = True
                    sentence.diagnostics.append(dg.make("COND_INCOMPLETE", clause.span))
                    clause.cond = Cond("ELSE", "CONSEQUENT", None)
                else:
                    clause.cond = Cond("ELSE", "CONSEQUENT", group[pos - 1].index)


def _link_condition(sentence, group, pos):
    ante = group[pos]
    peer = None
    if pos == 0 and len(group) > 1 and group[1].marker is None:
        peer = group[1]
    elif pos > 0:
        peer = group[pos - 1]
    ante.cond = Cond("IF", "ANTECEDENT", peer.index if peer else None)
    if peer is None:
        ante.tier2 = True
        sentence.diagnostics.append(dg.make("COND_INCOMPLETE", ante.span))
    elif peer.cond is None:
        peer.cond = Cond("IF", "CONSEQUENT", ante.index)
