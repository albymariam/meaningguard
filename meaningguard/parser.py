"""Island parser for English and Hindi (spec 03, 04).

Pipeline inside this module:

1. ``build_elements``  tokens plus assembled numbers become *elements* (values, relation words).
   Dates, durations and parts of the day are merged here (``12 November``, ``7 days``).
2. ``segment``         elements are split into clauses at commas, clause words and conditionals.
3. ``ClauseParser``    each clause is scanned left to right. A construct is recognised by its first
   element and consumes its operands (``from A to B``, ``A से B तक``, ``between A and B`` ...).
   Anything that no construct recognises is skipped (panic mode). Skipping a word outside a
   construct is free; skipping a relation word, or a word inside one, makes the parse tier 2.

The result is a raw ``Sentence`` AST. ``semantics.analyze`` and ``canon.canonicalize`` finish it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import diagnostics as dg
from . import numerics as num
from .lexer import (
    DAY,
    DAYPART,
    MONTH,
    NEG,
    NUMBER,
    PUNCT,
    REL,
    SCALE,
    SENT_END,
    TIME,
    WORD,
    LexResult,
)
from .lexicon import make_key
from .nodes import (
    ANCHOR_TYPES,
    Bound,
    Change,
    Clause,
    Count,
    Date,
    Daypart,
    Duration,
    Fact,
    Literal,
    Money,
    Neg,
    Percent,
    Quant,
    Range,
    Sentence,
    Time,
    TimeRel,
    Vague,
)

# ---------------------------------------------------------------------------------------------
# Tables

BOUND_ROLES = ("GT", "GTE", "LT", "LTE")
TIME_REL_ROLES = {
    "TIME_BEFORE": "BEFORE",
    "TIME_AFTER": "AFTER",
    "TIME_BY": "BY",
    "TIME_UNTIL": "UNTIL",
    "TIME_ON": "ON",
    "TIME_WITHIN": "WITHIN",
    "TIME_SINCE": "SINCE",
}
CHANGE_ROLES = ("RISE", "FALL", "RISE_NOUN", "FALL_NOUN")
PART_ROLES = ("RISE_PART", "FALL_PART")
OPERAND_TYPES = ("MONEY", "COUNT", "PERCENT", "DURATION")

# Finite past forms that state a change even with no number ("the fee increased"). Base forms and
# 'up'/'down' need an operand: "drop the form", "sit down" are not changes.
BARE_EN = {
    "rose",
    "risen",
    "increased",
    "grew",
    "fell",
    "fallen",
    "dropped",
    "decreased",
    "declined",
    "dipped",
    "plunged",
    "climbed",
    "jumped",
    "surged",
    "went up",
    "went down",
}
# Relation words that are ordinary particles when they have no operand: no tier penalty.
SILENT_ROLES = {
    "TO",
    "FROM",
    "TIME_ON",
    "TIME_BY",
    "TIME_WITHIN",
    "FROM_OR_THAN",
    "TO_UNTIL",
    "RANGE_AND",
    "CHANGE_COPULA",
    "RISE",
    "FALL",
    "RISE_NOUN",
    "FALL_NOUN",
    "RISE_PART",
    "FALL_PART",
    "APPROX",
}
# Words that may follow a clause-final negation without changing its scope: 'शुक्रवार के बाद नहीं है'.
COPULA_HI = {
    make_key(w)
    for w in (
        "है",
        "हैं",
        "था",
        "थी",
        "थे",
        "हो",
        "होगा",
        "होगी",
        "होंगे",
        "होता",
        "होती",
        "होते",
        "होनी",
        "होना",
        "चाहिए",
        "चाहिये",
        "सकता",
        "सकती",
        "सकते",
    )
}
LIGHT_VERBS_HI = {make_key(w) for w in ("हुई", "हुआ", "आई", "आया", "हुए", "हुईं")}
OR_WORDS = {"or", "either", make_key("या"), make_key("वा")}
SEP_WORDS = {
    "en": {"but", "while", "so", "yet"},
    "hi": {make_key(w) for w in ("लेकिन", "मगर", "जबकि", "इसलिए", "परंतु", "किन्तु")},
}
# After a day number following a month name, these words do not make the number a quantity.
DAY_FOLLOWERS = {
    "is",
    "are",
    "was",
    "will",
    "and",
    "to",
    "at",
    "by",
    "for",
    "the",
    "a",
    "an",
    "onwards",
    "only",
    make_key("से"),
    make_key("को"),
    make_key("तक"),
    make_key("पर"),
    make_key("है"),
}

_DIGITS = re.compile(r"\d+")
_NUM_DATE = re.compile(r"^(\d{1,4})[/-](\d{1,2})[/-](\d{1,4})$")


@lru_cache(maxsize=1)
def _duration_table() -> tuple:
    path = Path(__file__).parent / "lexicons" / "durations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    table = {}
    for entry in data["units"]:
        for lang in ("en", "hi"):
            for form in entry[lang]:
                table[tuple(make_key(w) for w in form.split())] = entry["unit"]
    return table, max(len(k) for k in table)


# ---------------------------------------------------------------------------------------------
# Elements


@dataclass
class El:
    kind: str  # VAL, REL, NEG, WORD, PUNCT, END, LIT, DAYPART
    start: int
    end: int
    text: str = ""
    key: str = ""
    value: object = None
    role: str | None = None
    attrs: dict = field(default_factory=dict)
    flags: set = field(default_factory=set)
    sentence: int = 0

    def __repr__(self) -> str:
        extra = self.value if self.kind == "VAL" else self.role
        return f"<{self.kind} {self.text!r} {extra}>"


def _item_value(item):
    if isinstance(item, num.Quantity):
        if item.type == "MONEY":
            return Money(item.value, item.currency)
        if item.type == "PERCENT":
            return Percent(item.value)
        return Count(item.value)
    if isinstance(item, num.TimeLiteral):
        return Time(item.hour, item.minute, item.period)
    return Vague(item.order)


def _numeric_date(text: str):
    m = _NUM_DATE.match(text)
    if not m:
        return None
    a, b, c = (int(g) for g in m.groups())
    assumed = False
    if len(m.group(1)) == 4:  # ISO: year first
        year, month, day = a, b, c
    else:
        year = c + 2000 if len(m.group(3)) <= 2 else c
        if a > 12:
            day, month = a, b
        elif b > 12:
            month, day = a, b
        else:
            day, month, assumed = a, b, True
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return Date(day=day, month=month, year=year), assumed


def build_elements(res: LexResult, nres: num.NumericResult) -> list:
    toks = res.tokens
    by_start = {item.start: item for item in nres.items}
    els: list = []
    j = 0
    while j < len(toks):
        item = by_start.get(j)
        if item is not None:
            flags = set(item.flags)
            for t in toks[item.start : item.end]:
                flags |= t.flags
            scales = [t for t in toks[item.start : item.end] if t.type == SCALE]
            attrs = {"words": getattr(item, "words", False), "scales": len(scales)}
            if len(scales) == 1:
                attrs["scale"] = scales[0].value
            els.append(
                El(
                    "VAL",
                    toks[item.start].start,
                    toks[item.end - 1].end,
                    item.text,
                    value=_item_value(item),
                    flags=flags,
                    sentence=toks[item.start].sentence,
                    attrs=attrs,
                )
            )
            j = max(item.end, j + 1)
            continue
        t = toks[j]
        j += 1
        base = dict(
            start=t.start,
            end=t.end,
            text=t.text,
            key=t.key,
            flags=set(t.flags),
            sentence=t.sentence,
        )
        if t.type == SENT_END:
            els.append(El("END", **base))
        elif t.type == PUNCT:
            els.append(El("PUNCT", **base))
        elif t.type == DAY:
            els.append(El("VAL", **base, value=Date(weekday=t.attrs["id"])))
        elif t.type == MONTH:
            els.append(El("VAL", **base, value=Date(month=t.attrs["id"]), attrs={"month": True}))
        elif t.type == DAYPART:
            els.append(El("DAYPART", **base, role=t.attrs["part"]))
        elif t.type == TIME:
            els.append(
                El(
                    "VAL",
                    **base,
                    value=Time(
                        t.attrs["hour"] % 24 if t.attrs["hour"] == 24 else t.attrs["hour"],
                        t.attrs["minute"],
                        None,
                    ),
                )
            )
        elif t.type == REL:
            els.append(El("REL", **base, role=t.attrs["role"], attrs=dict(t.attrs)))
        elif t.type == NEG:
            if t.attrs.get("counts"):
                els.append(El("NEG", **base, role=t.attrs["kind"], attrs=dict(t.attrs)))
            else:
                els.append(El("WORD", **base, attrs={"nonneg": t.attrs.get("role")}))
        elif t.type == WORD and t.attrs.get("opaque"):
            els.append(_opaque_el(t, base))
        else:
            if t.type == NUMBER:
                base["flags"].add("UNASSEMBLED_NUMBER")
            els.append(El("WORD", **base))
    return els


def _opaque_el(t, base) -> El:
    kind = t.attrs["opaque"]
    digits = t.attrs.get("digits", "")
    if not _DIGITS.search(digits):
        return El("WORD", **base)  # 'No.' marker or an ordinal word
    if kind == "DATE":
        parsed = _numeric_date(digits)
        if parsed:
            value, assumed = parsed
            flags = base["flags"] | ({"DATE_ORDER_ASSUMED"} if assumed else set())
            return El("VAL", **{**base, "flags": flags}, value=value)
        return El("LIT", **base, role="DATE", attrs={"text": digits})
    if kind in ("PHONE", "ORDINAL", "NUMBER_ABBREV"):
        text = "".join(_DIGITS.findall(digits))
    else:  # VERSION, IDENT
        text = digits
    return El("LIT", **base, role=kind, attrs={"text": text})


# -- merges -----------------------------------------------------------------------------------


def _is_val(el, *types) -> bool:
    return el is not None and el.kind == "VAL" and (not types or el.value.type in types)


def _int_in(el, lo, hi) -> int | None:
    if not _is_val(el, "COUNT") or el.value.amount != el.value.amount.to_integral_value():
        return None
    n = int(el.value.amount)
    return n if lo <= n <= hi else None


def _union(a: El, b: El, value, extra=()) -> El:
    return El(
        "VAL",
        min(a.start, b.start),
        max(a.end, b.end),
        "",
        value=value,
        flags=a.flags | b.flags | set(extra),
        sentence=a.sentence,
    )


def _day_of(el) -> int | None:
    """Day number from a plain integer or an ordinal literal (3rd, 3वाँ is not one)."""
    if el is None:
        return None
    if _int_in(el, 1, 31):
        return int(el.value.amount)
    if el.kind == "LIT" and el.role == "ORDINAL" and el.attrs.get("text", "").isdigit():
        n = int(el.attrs["text"])
        return n if 1 <= n <= 31 else None
    return None


def merge_dates(els: list) -> list:
    out: list = []
    i = 0
    while i < len(els):
        el = els[i]
        if not (el.kind == "VAL" and el.attrs.get("month")):
            out.append(el)
            i += 1
            continue
        month = el.value.month
        merged, day, year = el, None, None
        prev = out[-1] if out else None
        nxt = els[i + 1] if i + 1 < len(els) else None
        i += 1
        if (
            prev is not None
            and prev.kind == "WORD"
            and prev.key == "of"
            and len(out) > 1
            and out[-2].kind == "LIT"
        ):  # '3rd of March'
            out.pop()
            prev = out[-1]
        if prev is not None and prev.sentence == el.sentence and _day_of(prev):
            day = _day_of(prev)
            merged = _union(prev, el, None)
            out.pop()
        elif nxt is not None and _day_of(nxt) and nxt.sentence == el.sentence:
            after = els[i + 1] if i + 1 < len(els) else None
            if after is None or after.kind != "WORD" or after.key in DAY_FOLLOWERS:
                day = _day_of(nxt)
                merged = _union(el, nxt, None)
                i += 1
        # year: optional comma, then a 4-digit number
        probe = i
        if probe < len(els) and els[probe].kind == "PUNCT" and els[probe].text == "," and day:
            probe += 1
        if (
            probe < len(els)
            and _int_in(els[probe], 1900, 2100)
            and els[probe].sentence == el.sentence
        ):
            year = int(els[probe].value.amount)
            merged = _union(merged, els[probe], None)
            i = probe + 1
        merged.value = Date(day=day, month=month, year=year)
        out.append(merged)
    return out


def merge_durations(els: list) -> list:
    table, longest = _duration_table()
    out: list = []
    i = 0
    while i < len(els):
        el = els[i]
        if not _is_val(el, "COUNT"):
            out.append(el)
            i += 1
            continue
        j = i + 1
        glued_hyphen = (
            j + 1 < len(els)
            and els[j].kind == "PUNCT"
            and els[j].text == "-"
            and els[j].start == el.end
            and els[j + 1].start == els[j].end
        )
        if glued_hyphen:
            j += 1
        unit = None
        for n in range(longest, 0, -1):
            words = els[j : j + n]
            if len(words) == n and all(
                w.kind == "WORD" and w.sentence == el.sentence for w in words
            ):
                unit = table.get(tuple(w.key for w in words))
                if unit:
                    j += n
                    break
        if unit is None:
            out.append(el)
            i += 1
            continue
        out.append(_union(el, els[j - 1], Duration(el.value.amount, unit)))
        i = j
    return out


def _period_for(part: str, hour: int) -> str | None:
    if not 1 <= hour <= 12:
        return None
    if part == "morning":
        return "AM" if hour < 12 else None
    if part in ("noon", "evening"):
        return "PM"
    if part == "night":
        if hour == 12:
            return "AM"
        return "AM" if hour <= 5 else "PM"
    return None


def merge_dayparts(els: list) -> list:
    out: list = []
    i = 0
    while i < len(els):
        el = els[i]
        nxt = els[i + 1] if i + 1 < len(els) else None
        if el.kind == "DAYPART":
            if _is_val(nxt, "TIME") and nxt.value.period is None and nxt.sentence == el.sentence:
                period = _period_for(el.role, nxt.value.hour)
                if period:
                    t = nxt.value
                    merged = _union(
                        el, nxt, Time(t.hour, t.minute, period), {"PERIOD_FROM_DAYPART"}
                    )
                    out.append(merged)
                    i += 2
                    continue
            if (
                out
                and _is_val(out[-1], "TIME")
                and out[-1].value.period is None
                and out[-1].sentence == el.sentence
                and _period_for(el.role, out[-1].value.hour)
            ):
                prev = out.pop()
                t = prev.value
                out.append(
                    _union(
                        prev,
                        el,
                        Time(t.hour, t.minute, _period_for(el.role, t.hour)),
                        {"PERIOD_FROM_DAYPART"},
                    )
                )
                i += 1
                continue
            if (  # 'सोमवार की सुबह': the genitive between the day and the part of the day
                len(out) > 1
                and out[-1].kind == "WORD"
                and out[-1].key in (make_key("की"), make_key("के"))
                and _is_val(out[-2], "DATE")
                and out[-2].value.weekday is not None
                and out[-2].value.daypart is None
            ):
                out.pop()
            if (
                out
                and _is_val(out[-1], "DATE")
                and out[-1].value.weekday is not None
                and out[-1].value.daypart is None
                and out[-1].sentence == el.sentence
            ):
                prev = out.pop()
                d = prev.value
                out.append(_union(prev, el, Date(d.weekday, d.day, d.month, d.year, el.role)))
                i += 1
                continue
            out.append(
                El(
                    "VAL",
                    el.start,
                    el.end,
                    el.text,
                    value=Daypart(el.role),
                    flags=el.flags,
                    sentence=el.sentence,
                )
            )
            i += 1
            continue
        out.append(el)
        i += 1
    return out


# ---------------------------------------------------------------------------------------------
# Segmentation


@dataclass
class ClauseEls:
    els: list
    marker: str | None
    sentence: int


def segment(els: list, lang: str) -> list:
    """Split elements into clauses. Conditional words open a clause and are removed."""
    clauses: list = []
    cur: list = []
    marker: str | None = None
    ante_open = False
    between_open = False
    sent = 0
    sep_words = SEP_WORDS[lang]
    then_after = _then_after(els)

    def flush():
        nonlocal cur, marker, ante_open, between_open
        if cur:
            clauses.append(ClauseEls(cur, marker, cur[0].sentence))
            marker = None
        cur = []
        ante_open = False
        between_open = False

    def tail_has_between(idx: int) -> bool:
        for el in els[idx + 1 : idx + 6]:
            if el.kind in ("END", "PUNCT") or el.role in ("COND_IF", "COND_THEN"):
                return False
            if el.kind == "REL" and el.role == "BETWEEN":
                return True
        return False

    for idx, el in enumerate(els):
        sent = el.sentence
        if el.kind == "END":
            flush()
            marker = None
            continue
        if el.kind == "PUNCT" and el.text in ",;":
            flush()
            continue
        if el.kind == "REL" and el.role == "BETWEEN":
            between_open = True
        if el.kind == "REL" and el.role == "RANGE_AND":
            if between_open:
                between_open = False
                cur.append(el)
            else:
                flush()
            continue
        if el.kind == "WORD" and el.key in sep_words:
            flush()
            continue
        if el.kind == "WORD" and lang == "hi" and el.key == make_key("और"):
            if cur and cur[-1].kind == "VAL" and tail_has_between(idx):
                cur.append(el)
            else:
                flush()
            continue
        if el.kind == "REL" and el.role in ("COND_IF", "COND_UNLESS", "COND_ELSE"):
            if el.role == "COND_IF" and el.key == make_key("जो") and not then_after.get(idx):
                el.kind = "WORD"
                el.role = None
                cur.append(el)
                continue
            flush()
            if el.role == "COND_IF":
                marker = "IF"
            elif el.role == "COND_ELSE":
                marker = "ELSE"
            else:
                marker = "JABTAK" if el.key == make_key("जब तक") else "UNLESS"
            ante_open = el.role != "COND_ELSE"
            continue
        if el.kind == "REL" and el.role == "COND_THEN":
            if ante_open:
                flush()
                continue
            if (
                not cur
                and clauses
                and clauses[-1].marker in ("IF", "UNLESS", "JABTAK")
                and clauses[-1].sentence == sent
            ):
                continue
            el.kind = "WORD"
            el.role = None
            cur.append(el)
            continue
        cur.append(el)
    flush()
    return clauses


def _then_after(els: list) -> dict:
    """For each index: is there a COND_THEN later in the same sentence?"""
    out: dict = {}
    seen_then = False
    for idx in range(len(els) - 1, -1, -1):
        el = els[idx]
        if el.kind == "END":
            seen_then = False
        elif el.kind == "REL" and el.role == "COND_THEN":
            seen_then = True
        out[idx] = seen_then
    return out


# ---------------------------------------------------------------------------------------------
# Clause parser


def _share(lo_el: El, hi_el: El) -> tuple:
    """Shared scale and shared AM/PM inside one range: ``₹5-10 lakh`` is 5 lakh to 10 lakh,
    ``5 to 7 PM`` is 5 PM to 7 PM. Returns ``(low, high, flags)``."""
    lo, hi, flags = lo_el.value, hi_el.value, set()
    if (
        lo.type in ("COUNT", "MONEY")
        and hi.type in ("COUNT", "MONEY")
        and lo_el.attrs.get("scales", 0) == 0
        and hi_el.attrs.get("scales") == 1
        and lo.amount < hi.amount / hi_el.attrs["scale"]  # '5-10 lakh', never '10-5 lakh'
    ):
        amount = lo.amount * hi_el.attrs["scale"]
        lo = Money(amount, lo.currency) if lo.type == "MONEY" else Count(amount)
        flags.add("SCALE_SHARED")
    elif (
        lo.type == "COUNT"
        and hi.type == "TIME"
        and lo.amount == lo.amount.to_integral_value()
        and 0 <= lo.amount <= 24
    ):
        hour = int(lo.amount)
        period = hi.period if (hour == 12 or hour <= hi.hour) else None
        lo = Time(hour, 0, period)
        if period:
            flags.add("PERIOD_SHARED")
    elif (
        lo.type == "TIME"
        and hi.type == "TIME"
        and lo.period is not None
        and hi.period is None
        and 1 <= hi.hour <= 12
        and (lo.hour == 12 or lo.hour < hi.hour)
        and hi.hour != 12
    ):  # 'सुबह 9 बजे से 11 बजे तक': both ends are in the morning
        hi = Time(hi.hour, hi.minute, lo.period)
        flags.add("PERIOD_SHARED")
    return lo, hi, flags


def _compatible(a, b) -> bool:
    ta, tb = a.type, b.type
    if ta == tb:
        return ta in OPERAND_TYPES + ("TIME", "DATE")
    pair = {ta, tb}
    return (
        "COUNT" in pair
        and bool(pair & {"MONEY", "PERCENT", "DURATION"})
        and pair <= set(OPERAND_TYPES)
    )


class ClauseParser:
    def __init__(self, lang: str, els: list):
        self.lang = lang
        self.els = els
        self.n = len(els)
        self.used: set = set()
        self.facts: list = []  # (start_idx, end_idx, fact)
        self.diags: list = []
        self.tier2 = False
        self.flags: set = set()

    # -- helpers ----------------------------------------------------------------------------
    def at(self, i):
        return self.els[i] if 0 <= i < self.n and i not in self.used else None

    def rel(self, i, *roles):
        el = self.at(i)
        return (
            el if el is not None and el.kind == "REL" and (not roles or el.role in roles) else None
        )

    def val(self, i, *types):
        el = self.at(i)
        return (
            el
            if el is not None and el.kind == "VAL" and (not types or el.value.type in types)
            else None
        )

    def word(self, i, key=None):
        el = self.at(i)
        return (
            el if el is not None and el.kind == "WORD" and (key is None or el.key == key) else None
        )

    def punct(self, i, text):
        el = self.at(i)
        return el if el is not None and el.kind == "PUNCT" and el.text == text else None

    def val_approx(self, i, *types):
        """(element, next index, approx?) for ``[APPROX] VALUE`` at i."""
        el = self.rel(i, "APPROX")
        if el is not None and self.val(i + 1, *types):
            return self.els[i + 1], i + 2, True
        v = self.val(i, *types)
        return (v, i + 1, False) if v else None

    def tail_to(self, i, roles, limit=2):
        """Index of a REL with one of ``roles`` at i, allowing up to ``limit`` unit words first."""
        for skip in range(limit + 1):
            if all(self.word(i + k) for k in range(skip)) and self.rel(i + skip, *roles):
                return i + skip
        return None

    def mark(self, a, b):
        self.used.update(range(a, b))

    def span_of(self, a, b):
        return (self.els[a].start, self.els[b - 1].end)

    def add(self, fact: Fact, a: int, b: int, flags=()):
        for k in range(a, b):
            if self.els[k].kind == "VAL":
                fact.flags |= self.els[k].flags
        fact.flags |= set(flags)
        fact.span = self.span_of(a, b)
        self.mark(a, b)
        self.facts.append((a, b, fact))
        return b

    def diag(self, code, a, b=None, message=None):
        b = a + 1 if b is None else b
        self.diags.append(dg.make(code, self.span_of(a, b), message))

    def penalise(self, code, a, b=None):
        self.tier2 = True
        self.diag(code, a, b)

    # -- main loop ---------------------------------------------------------------------------
    def parse(self):
        i = 0
        while i < self.n:
            if i in self.used:
                i += 1
                continue
            end = self.dispatch(i)
            if end is None:
                self.unattached(i)
                i += 1
            else:
                i = max(end, i + 1)
        self.finish()
        return self

    def dispatch(self, i):
        el = self.els[i]
        if el.kind == "VAL":
            return self.val_start(i, i, False)
        if el.kind == "LIT":
            return self.add(Literal(lit=el.role, text=el.attrs["text"]), i, i + 1, el.flags)
        if el.kind == "NEG":
            return self.neg_start(i)
        if el.kind == "REL":
            if el.role == "APPROX":
                if self.val(i + 1):
                    return self.val_start(i + 1, i, True)
                return None
            return self.rel_start(i)
        return None

    def unattached(self, i):
        el = self.els[i]
        if (
            el.kind == "REL"
            and el.role not in SILENT_ROLES
            and el.role not in ("COND_IF", "COND_THEN", "COND_ELSE", "COND_UNLESS")
        ):
            self.penalise("UNATTACHED_REL", i)

    # -- negation ----------------------------------------------------------------------------
    def neg_start(self, i):
        el = self.els[i]
        strength = "WEAK" if el.role == "NEG_WEAK" else "STRONG"
        if el.role == "NEG_PREP":
            val = self.val(i + 1)
            if val is not None:
                return self.add(Neg(scope="QUANT", strength=strength, target=val.value), i, i + 2)
            words = []
            k = i + 1
            while len(words) < 2 and self.word(k):
                words.append(self.els[k].text)
                k += 1
            neg = Neg(scope="QUANT", strength=strength, target=" ".join(words) or None)
            return self.add(neg, i, i + 1)
        nxt = self.rel(i + 1)
        if (
            self.lang == "en"
            and nxt is not None
            and (nxt.role in BOUND_ROLES or nxt.role in TIME_REL_ROLES)
            and nxt.role != "TIME_ON"
        ):
            before = len(self.facts)
            end = self.rel_start(i + 1)
            if end is not None and len(self.facts) == before + 1:
                a, b, inner = self.facts.pop()
                if isinstance(inner, (Bound, TimeRel)):
                    neg = Neg(scope="REL", strength=strength, target=inner)
                    self.mark(i, i + 1)
                    return self.add(neg, i, b, inner.flags)
                self.facts.append((a, b, inner))
        return self.add(Neg(scope="CLAUSE", strength=strength), i, i + 1)

    # -- relation words that start a construct ------------------------------------------------
    def rel_start(self, i):
        role = self.els[i].role
        if role in CHANGE_ROLES and self.lang == "en":
            return self.en_change(i)
        if role in CHANGE_ROLES:
            return self.hi_bare_change(i)
        if role in PART_ROLES:
            return self.hi_participle(i, None, i)
        if role == "FROM" and self.lang == "en":
            return self.en_from_to(i)
        if role == "BETWEEN" and self.lang == "en":
            return self.en_between(i)
        if role in BOUND_ROLES:
            return self.bound_pre(i)
        if role in TIME_REL_ROLES and self.lang == "en":
            return self.time_pre(i)
        return None

    def bound_pre(self, i):
        el = self.els[i]
        if el.attrs.get("position") == "post" and self.lang == "en":
            return None  # 'or more' follows its value; handled from the value
        if el.role in ("GT", "LT") and self.lang == "hi":
            return None  # Hindi GT and LT follow their value
        got = self.val_approx(i + 1)
        if got is None or got[0].value.type not in OPERAND_TYPES + ("TIME", "DATE", "VAGUE"):
            return None
        val, j, approx = got
        flags = {"APPROX_IN_COMPOUND"} if approx else set()
        return self.add(Bound(op=el.role, value=val.value), i, j, flags)

    def time_pre(self, i):
        el = self.els[i]
        anchor = self.val(i + 1)
        if anchor is None or anchor.value.type not in ANCHOR_TYPES:
            return None
        return self.add(TimeRel(rel=TIME_REL_ROLES[el.role], anchor=anchor.value), i, i + 2)

    # -- English constructs --------------------------------------------------------------------
    def en_operand(self, k):
        """Operand of a change word at k: returns (kwargs, end) or None."""
        if self.rel(k, "FROM"):
            got = self.val_approx(k + 1, *OPERAND_TYPES)
            if got is None:
                return None
            v1, j, _ = got
            to_at = self.tail_to(j, ("TO",))
            if to_at is not None:
                got2 = self.val_approx(to_at + 1, *OPERAND_TYPES)
                if got2 is not None:
                    return {"frm": v1.value, "to": got2[0].value}, got2[1]
            return {"frm": v1.value}, j
        if self.rel(k, "TO"):
            got = self.val_approx(k + 1, *OPERAND_TYPES)
            if got is None:
                return None
            origin = (
                self.val_approx(got[1] + 1, *OPERAND_TYPES) if self.rel(got[1], "FROM") else None
            )
            if origin is not None:  # 'climbed to 8% from 6%'
                return {"frm": origin[0].value, "to": got[0].value}, origin[1]
            return {"to": got[0].value}, got[1]
        if self.rel(k, "TIME_BY") or self.word(k, "of"):
            got = self.val_approx(k + 1, *OPERAND_TYPES)
            return ({"delta": got[0].value}, got[1]) if got else None
        got = self.val_approx(k, *OPERAND_TYPES)
        return ({"delta": got[0].value}, got[1]) if got else None

    def en_change(self, i):
        el = self.els[i]
        direction = "RISE" if el.role.startswith("RISE") else "FALL"
        if self.rel(i + 1, *BOUND_ROLES):
            self.mark(i, i + 1)  # "falls below 400": the bound carries the meaning
            return i + 1
        for skip in range(3):
            if not all(self.word(i + 1 + s) for s in range(skip)):
                break
            found = self.en_operand(i + 1 + skip)
            if found:
                kwargs, end = found
                change = Change(direction=direction, **kwargs)
                if skip:
                    self.penalise("SKIPPED_IN_ISLAND", i + 1, i + 1 + skip)
                return self.add(change, i, end)
        if el.role in ("RISE", "FALL") and el.key in BARE_EN:
            return self.add(Change(direction=direction), i, i + 1)
        return None

    def make_range(self, lo_el, hi_el, form, start, end):
        """Build a RANGE from two value elements, sharing a scale or an AM/PM across the ends."""
        low, high, flags = _share(lo_el, hi_el)
        if not _compatible(low, high):
            return None
        return self.add(Range(low=low, high=high, source_form=form), start, end, flags)

    def en_from_to(self, i):
        got = self.val_approx(i + 1)
        if got is None:
            return None
        v1, j, _ = got
        to_at = self.tail_to(j, ("TO",))
        if to_at is None:
            return None
        got2 = self.val_approx(to_at + 1)
        if got2 is None:
            return None
        return self.make_range(v1, got2[0], "FROM_TO", i, got2[1])

    def en_between(self, i):
        got = self.val_approx(i + 1)
        if got is None:
            return None
        v1, j, _ = got
        and_at = self.tail_to(j, ("RANGE_AND",))
        if and_at is None:
            return None
        got2 = self.val_approx(and_at + 1)
        if got2 is None:
            return None
        return self.make_range(v1, got2[0], "BETWEEN", i, got2[1])

    # -- constructs that start at a value --------------------------------------------------------
    def val_start(self, i, start, approx):
        if self.lang == "en":
            end = self.en_val(i, start, approx)
        else:
            end = self.hi_val(i, start, approx)
        if end is not None:
            return end
        return self.standalone(i, start, approx)

    def standalone(self, i, start, approx):
        value = self.els[i].value
        if value.type == "DAYPART":
            return (
                None  # 'this morning', 'a night': a bare part of the day states no checkable fact
            )
        if value.type in ("TIME", "DATE"):
            return self.add(TimeRel(rel="ON", anchor=value), start, i + 1, {"IMPLICIT_ON"})
        if (
            value.type in ("COUNT", "DURATION")
            and value.amount == 1
            and "WEAK_ONE" in self.els[i].flags
        ):  # 'एक कप', 'a year': may be an article, so it is not a fact (spec 01 ASM-8)
            self.diag("WEAK_ONE_DROPPED", i)
            return i + 1
        return self.add(Quant(value=value, approx=approx), start, i + 1)

    def _range_from(self, i, start, j, form):
        """Value at i, separator already matched, second value at j."""
        got = self.val_approx(j)
        if got is None:
            return None
        return self.make_range(self.els[i], got[0], form, start, got[1])

    def en_val(self, i, start, approx):
        v = self.els[i].value
        # A to B
        to_at = self.tail_to(i + 1, ("TO",))
        if to_at is not None:
            end = self._range_from(i, start, to_at + 1, "TO")
            if end is not None:
                return end
        # A - B
        if self.punct(i + 1, "-"):
            end = self._range_from(i, start, i + 2, "HYPHEN")
            if end is not None:
                return end
        # 12% rise / 5% growth / another 3% rise
        nxt = self.rel(i + 1, *CHANGE_ROLES)
        if nxt is not None and v.type in OPERAND_TYPES:
            direction = "RISE" if nxt.role.startswith("RISE") else "FALL"
            return self.add(Change(direction=direction, delta=v), start, i + 2)
        # 75% or more
        post = self.rel(i + 1, "GTE", "LTE")
        if post is not None and post.attrs.get("position") == "post":
            flags = {"APPROX_IN_COMPOUND"} if approx else set()
            return self.add(Bound(op=post.role, value=v), start, i + 2, flags)
        return None

    # -- Hindi constructs ---------------------------------------------------------------
    def hi_bare_change(self, i):
        el = self.els[i]
        if el.role in ("RISE", "FALL"):
            return self.add(Change(direction=el.role), i, i + 1)
        return None

    def hi_participle(self, i, frm_el, start):
        """``[Value से] PART Value [COPULA]`` with the participle at index i."""
        el = self.els[i]
        direction = "RISE" if el.role == "RISE_PART" else "FALL"
        got = self.val_approx(i + 1, *OPERAND_TYPES)
        if got is None:
            return None
        end = got[1]
        if self.rel(end, "CHANGE_COPULA"):
            end += 1
        change = Change(direction=direction, frm=frm_el.value if frm_el else None, to=got[0].value)
        return self.add(change, start, end)

    def hi_val(self, i, start, approx):
        el = self.els[i]
        v = el.value
        numeric = v.type in OPERAND_TYPES
        # V [noun] से PART V [COPULA]
        k = i + 1 + (1 if self.word(i + 1) and self.rel(i + 2, "FROM_OR_THAN") else 0)
        if numeric and self.rel(k, "FROM_OR_THAN") and self.rel(k + 1, *PART_ROLES):
            end = self.hi_participle(k + 1, el, start)
            if end is not None:
                return end
        # V से बढ़कर V ... : the GT token read as the change participle
        gt = self.rel(i + 1, "GT")
        if (
            numeric
            and gt is not None
            and gt.attrs.get("alt_split")
            and self.val(i + 2, *OPERAND_TYPES)
        ):
            got = self.val_approx(i + 2, *OPERAND_TYPES)
            end = got[1]
            if self.rel(end, "CHANGE_COPULA"):
                end += 1
            self.diag("AMBIGUOUS_PARTICIPLE", i + 1)
            return self.add(Change(direction="RISE", frm=v, to=got[0].value), start, end)
        # V से V [तक] [verb]
        if self.rel(i + 1, "FROM_OR_THAN"):
            got = self.val_approx(i + 2)
            low, high, shared = _share(el, got[0]) if got is not None else (None, None, set())
            if got is not None and _compatible(low, high):
                end = got[1]
                tak = self.tail_to(end, ("TO_UNTIL",), limit=1)
                if tak is not None:
                    end = tak + 1
                verb = self.rel(end, "RISE", "FALL")
                if verb is not None and numeric:
                    change = Change(direction=verb.role, frm=low, to=high)
                    return self.add(change, start, end + 1, shared)
                return self.add(
                    Range(low=low, high=high, source_form="FROM_TO"), start, end, shared
                )
        # V तक <verb>  |  V तक (up to)
        tak = self.tail_to(i + 1, ("TO_UNTIL",), limit=1) if numeric else None
        if tak is not None:  # 'प्रति ग्राहक 5 आइटम तक': one unit noun may stand before तक
            verb = self.rel(tak + 1, "RISE", "FALL")
            if verb is not None:
                return self.add(Change(direction=verb.role, to=v), start, tak + 2)
            if v.type != "DURATION":
                return self.add(Bound(op="LTE", value=v), start, tak + 1, {"TAK_AS_UPTO"})
        # V [की] <change word> [light verb]
        if numeric:
            k = i + 1
            if self.word(k, make_key("की")):
                k += 1
            word = self.rel(k, *CHANGE_ROLES)
            if word is not None and (k == i + 1 or word.role.endswith("NOUN")):
                end = k + 1
                if (
                    word.role.endswith("NOUN")
                    and self.word(end)
                    and self.els[end].key in LIGHT_VERBS_HI
                ):
                    end += 1
                direction = "RISE" if word.role.startswith("RISE") else "FALL"
                return self.add(Change(direction=direction, delta=v), start, end)
        # V <bound word>
        post = self.rel(i + 1, *BOUND_ROLES)
        if post is not None and (v.type in OPERAND_TYPES + ("TIME", "DATE")):
            return self.add(Bound(op=post.role, value=v), start, i + 2)
        # V और V के बीच
        if self.word(i + 1, make_key("और")):
            got = self.val_approx(i + 2)
            if got is not None and self.rel(got[1], "BETWEEN"):
                end = self.make_range(el, got[0], "BETWEEN", start, got[1] + 1)
                if end is not None:
                    return end
        # V - V
        if self.punct(i + 1, "-"):
            end = self._range_from(i, start, i + 2, "HYPHEN")
            if end is not None:
                return end
        # anchors: Date तक, Date के बाद, 10 बजे ...
        if v.type in ANCHOR_TYPES:
            post = self.rel(i + 1, *[r for r in TIME_REL_ROLES] + ["TO_UNTIL"])
            if post is not None:
                role = post.role
                if role == "TO_UNTIL":
                    rel = "UNTIL"
                elif role == "TIME_WITHIN" and v.type != "DURATION":
                    rel = "ON"  # 'अक्टूबर में' is 'in October'
                else:
                    rel = TIME_REL_ROLES[role]
                if v.type == "DURATION" and role == "TO_UNTIL":
                    return None
                return self.add(TimeRel(rel=rel, anchor=v), start, i + 2)
            return None
        # a lone से after a number
        if numeric and self.rel(i + 1, "FROM_OR_THAN"):
            self.penalise("AMBIGUOUS_FROM", i + 1)
            return self.add(Quant(value=v, role="AMBIGUOUS", approx=approx), start, i + 2)
        return None

    # -- finishing ----------------------------------------------------------------------
    def finish(self):
        self._wrap_hindi_neg_rel()
        self._neg_targets()
        self._unsupported()
        self.facts.sort(key=lambda t: t[0])

    def _wrap_hindi_neg_rel(self):
        """Hindi adjacency rule: a negation that ends the clause (optionally before a copula such
        as 'है') and directly follows a time or bound phrase scopes over that phrase only."""
        if self.lang != "hi":
            return
        meaningful = [k for k in range(self.n) if self.els[k].kind not in ("PUNCT", "END")]
        for idx, (a, b, fact) in enumerate(list(self.facts)):
            if not (isinstance(fact, Neg) and fact.scope == "CLAUSE" and self.els[a].kind == "NEG"):
                continue
            after = [k for k in meaningful if k > a]
            if not all(self.els[k].kind == "WORD" and self.els[k].key in COPULA_HI for k in after):
                continue
            before = [k for k in meaningful if k < a]
            if not before:
                continue
            for pidx, (pa, pb, pf) in enumerate(self.facts):
                gap = [k for k in before if k >= pb]
                near = pb - 1 == before[-1] or (
                    isinstance(pf, Bound)  # '4 से अधिक मेहमानों की अनुमति नहीं है'
                    and pb <= before[-1]
                    and len(gap) <= 3
                    and all(self.els[k].kind == "WORD" and k not in self.used for k in gap)
                    and sum(isinstance(f, (Bound, TimeRel)) for _, _, f in self.facts) == 1
                )
                if isinstance(pf, (Bound, TimeRel)) and near:
                    fact.scope, fact.target = "REL", pf
                    fact.span = (pf.span[0], fact.span[1])
                    fact.flags |= pf.flags
                    self.facts[idx] = (pa, b, fact)
                    del self.facts[pidx]
                    return

    def _neg_targets(self):
        """The opaque predicate word of a clause negation: next word, else the nearest before."""
        for a, b, fact in self.facts:
            if isinstance(fact, Neg) and fact.scope == "CLAUSE" and fact.target is None:
                after = [k for k in range(b, self.n) if self.word(k)]
                before = [k for k in range(a - 1, -1, -1) if self.word(k)]
                pick = (after or before or [None])[0]
                if pick is not None:
                    fact.target = self.els[pick].text

    def _unsupported(self):
        has_or = [
            k for k in range(self.n) if self.els[k].kind == "WORD" and self.els[k].key in OR_WORDS
        ]
        if has_or and self.facts:
            self.penalise("UNSUPPORTED_STRUCTURE", has_or[0])

    def pred_text(self) -> str | None:
        parts = []
        for k, el in enumerate(self.els):
            if el.kind == "WORD" and k not in self.used and not el.attrs.get("nonneg"):
                parts.append(el.text)
            elif el.kind == "REL" and el.role in ("RISE", "FALL", "RISE_NOUN", "FALL_NOUN"):
                parts.append(el.text)
        return " ".join(parts) or None


# ---------------------------------------------------------------------------------------------
# Entry point


def parse(text: str, lang: str) -> tuple:
    """Parse one input. Returns ``(Sentence, LexResult, NumericResult)``; the AST is still raw."""
    if lang not in ("en", "hi"):
        raise ValueError(f"unsupported language {lang!r}")
    res, nres = num.analyze(text, lang)
    els = merge_dayparts(merge_durations(merge_dates(build_elements(res, nres))))
    if lang == "hi":
        els = _hindi_idioms(els)
    diagnostics: list = []
    clauses: list = []
    for idx, ce in enumerate(segment(els, lang)):
        clause = Clause(
            index=idx,
            sentence=ce.sentence,
            marker=ce.marker,
            span=(ce.els[0].start, ce.els[-1].end),
        )
        try:
            cp = ClauseParser(lang, ce.els).parse()
            clause.facts = [fact for _, _, fact in cp.facts]
            clause.pred_text = cp.pred_text()
            clause.tier2 = cp.tier2
            diagnostics.extend(cp.diags)
        except Exception as exc:  # panic mode of last resort: keep lexer-level facts
            clause.facts = _lexer_only_facts(ce.els)
            clause.tier2 = True
            clause.flags.add("INTERNAL_PARSE_ERROR")
            diagnostics.append(
                dg.make("INTERNAL_PARSE_ERROR", clause.span, f"{type(exc).__name__}: {exc}")
            )
        for el in ce.els:
            if "UNASSEMBLED_NUMBER" in el.flags:
                diagnostics.append(dg.make("UNASSEMBLED_NUMBER", (el.start, el.end)))
                clause.tier2 = True
        clauses.append(clause)
    sentence = Sentence(lang=lang, text=res.text, clauses=clauses, diagnostics=diagnostics)
    return sentence, res, nres


_KUCH, _BHI, _LEKAR = make_key("कुछ"), make_key("भी"), make_key("लेकर")
_FARK = {make_key(w) for w in ("फर्क", "फ़र्क", "फरक")}


def _hindi_idioms(els: list) -> list:
    """Hindi phrases whose parts must not be read on their own: 'से लेकर' (from ... to),
    'कुछ भी' (anything, not a vague amount), 'फर्क नहीं पड़ता' (it does not matter)."""
    out: list = []
    for k, el in enumerate(els):
        prev = out[-1] if out else None
        nxt = els[k + 1] if k + 1 < len(els) else None
        if (
            el.kind == "WORD"
            and el.key == _LEKAR
            and prev is not None
            and prev.role == "FROM_OR_THAN"
        ):
            continue
        if el.kind == "VAL" and el.value.type == "VAGUE" and nxt is not None and nxt.key == _BHI:
            el = El("WORD", el.start, el.end, el.text, key=_KUCH, flags=set(), sentence=el.sentence)
        elif el.kind == "NEG" and prev is not None and prev.kind == "WORD" and prev.key in _FARK:
            el = El(
                "WORD", el.start, el.end, el.text, key=el.key, flags=set(), sentence=el.sentence
            )
        out.append(el)
    return out


def _lexer_only_facts(els: list) -> list:
    facts = []
    for el in els:
        if el.kind == "VAL" and el.value.type in OPERAND_TYPES + ("VAGUE",):
            facts.append(Quant(value=el.value, span=(el.start, el.end), flags=set(el.flags)))
        elif el.kind == "NEG":
            facts.append(Neg(scope="CLAUSE", span=(el.start, el.end)))
    return facts
