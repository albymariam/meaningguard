"""Assembles number expressions from lexer tokens (spec 01 NUM-6 to NUM-11, 02 sections 1, 4).

Everything is ``Decimal``. A number expression is evaluated left to right:

* English and Hindi number words, digits and scales combine (``2 crore 50 lakh``,
  ``five hundred and five``, ``दो सौ पाँच``);
* scales must strictly decrease (``SCALE_ORDER`` error otherwise);
* Hindi fraction words (सवा, साढ़े, पौने, डेढ़, ढाई, आधा) and English ``half a million``;
* clock times (``10:30 AM``, ``५ बजे``, ``साढ़े पाँच बजे`` = 5:30) become ``TimeLiteral``;
* currency tokens attach to the nearest unclaimed number (spec currency.json rules).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .lexer import (
    CURRENCY,
    NUMBER,
    NUMWORD,
    PERCENT,
    PUNCT,
    REL,
    SCALE,
    SENT_END,
    TIME,
    TIMEMARK,
    VAGUE,
    WORD,
    LexResult,
    lex,
)


def clean(value: Decimal) -> Decimal:
    """Exact value without trailing zeros or exponent: 6.0 -> 6, 250000.0 -> 250000, 1.50 -> 1.5."""
    if value == value.to_integral_value():
        return Decimal(int(value))
    return value.normalize()


ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)

_ONE_WORDS = {"one", "एक"}
_ONE_PRONOUN_BEFORE = {"the", "this", "that", "which", "every", "any", "each", "such", "who"}
_ONE_PRONOUN_AFTER = {"of", "another", "by", "ones", "'s"}
_EN_FRACTION_PHRASES = {"a half", "half", "a quarter", "quarter", "three quarters"}
_BARE_CURRENCY_ABBREV = {"rs", "rs.", "रु", "रु.", "रू", "inr"}


@dataclass
class Quantity:
    start: int  # token index range [start, end)
    end: int
    text: str
    orig: tuple
    value: Decimal
    type: str  # COUNT | MONEY | PERCENT
    currency: str | None
    flags: set = field(default_factory=set)
    words: bool = False  # built from number words rather than digits

    @property
    def kind(self) -> str:
        return "QUANTITY"


@dataclass
class TimeLiteral:
    start: int
    end: int
    text: str
    orig: tuple
    hour: int
    minute: int
    period: str | None  # "AM", "PM" or None
    flags: set = field(default_factory=set)

    @property
    def kind(self) -> str:
        return "TIME"


@dataclass
class Vague:
    start: int
    end: int
    text: str
    orig: tuple
    order: int | None
    flags: set = field(default_factory=set)

    @property
    def kind(self) -> str:
        return "VAGUE"


@dataclass
class NumericError:
    code: str
    start: int
    end: int
    text: str


@dataclass
class NumericResult:
    items: list
    errors: list
    flags: set

    @property
    def quantities(self) -> list:
        return [i for i in self.items if i.kind == "QUANTITY"]

    @property
    def times(self) -> list:
        return [i for i in self.items if i.kind == "TIME"]

    @property
    def vague(self) -> list:
        return [i for i in self.items if i.kind == "VAGUE"]


@dataclass
class _Expr:
    end: int
    value: Decimal | None = None
    flags: set = field(default_factory=set)
    error: str | None = None
    words: bool = False
    single: object = None  # the one token, when the expression is a single number word
    hfrac: tuple | None = None  # (rule, N, M) for Hindi fraction groups
    standalone_fraction: bool = False


class _Assembler:
    def __init__(self, result: LexResult):
        self.res = result
        self.toks = result.tokens
        self.n = len(self.toks)
        self.items: list = []
        self.errors: list = []
        self.between_open = False

    # -- text helpers -----------------------------------------------------------------------
    def _text(self, a: int, b: int) -> str:
        return self.res.text[self.toks[a].start : self.toks[b - 1].end]

    def _orig(self, a: int, b: int) -> tuple:
        return (self.toks[a].orig[0], self.toks[b - 1].orig[1])

    def _tok(self, j):
        return self.toks[j] if 0 <= j < self.n else None

    # -- number expressions -----------------------------------------------------------------
    def _is_scale(self, tok) -> bool:
        return tok is not None and tok.type == SCALE

    def _parse_expr(self, i: int):
        toks, n = self.toks, self.n
        start = i
        j = i
        article = False
        first = toks[j]
        if first.type == WORD and first.key in ("a", "an") and self._is_scale(self._tok(j + 1)):
            article = True
            j += 1
        first = toks[j]
        if first.type == NUMWORD and first.attrs.get("kind") == "hfrac":
            return self._parse_hfrac(j, start)
        if first.type == NUMWORD and first.attrs.get("kind") == "frac":
            return self._parse_en_fraction(j, start)

        total, current = ZERO, None
        last_major = None
        small_limit = 100
        consumed = 0
        flags: set = set()
        words = False
        after_scale = False
        last_token = None
        single = None
        while j < n:
            t = toks[j]
            if t.type == PUNCT and t.text == "-" and small_limit == 10 and last_token is not None:
                nxt = self._tok(j + 1)
                glued = nxt is not None and last_token.end == t.start == nxt.start - 1
                if glued and nxt.type == NUMWORD:
                    j += 1
                    continue
            if t.type == NUMBER:
                if current is not None:
                    break
                if consumed and not self._digits_group_follows(j):
                    break
                current = t.value
                flags |= t.flags
                small_limit = 0
                after_scale = False
                nxt = self._tok(j + 1)
                if (
                    nxt is not None and nxt.type == NUMWORD and nxt.attrs.get("unicode")
                    and nxt.start == t.end
                ):
                    current += nxt.value
                    j += 1
                    consumed += 1
                consumed += 1
                last_token = t
                j += 1
                continue
            if t.type == NUMWORD and t.attrs.get("kind") in ("unit", "tens"):
                v = t.value
                lang = t.attrs.get("lang")
                kind = t.attrs["kind"]
                if lang == "hi":
                    if small_limit != 100:
                        break
                    current = (current or ZERO) + v
                    small_limit = 0
                elif kind == "tens":
                    if small_limit != 100:
                        break
                    current = (current or ZERO) + v
                    small_limit = 10
                else:
                    if small_limit == 100:
                        current = (current or ZERO) + v
                        small_limit = 0
                    elif small_limit == 10 and ONE <= v <= 9:
                        current = (current or ZERO) + v
                        small_limit = 0
                    else:
                        break
                words = True
                flags |= t.flags
                if consumed == 0:
                    single = t
                else:
                    single = None
                consumed += 1
                last_token = t
                after_scale = False
                j += 1
                continue
            if t.type == SCALE:
                m = t.value
                if t.attrs.get("exact_unit"):
                    current = (current if current is not None else ONE) * m
                    small_limit = 0
                elif m == HUNDRED:
                    if current is None:
                        current = HUNDRED
                    elif current < HUNDRED:
                        current *= HUNDRED
                    else:
                        break
                    small_limit = 100
                else:
                    if last_major is not None and m >= last_major:
                        return _Expr(end=j + 1, error="SCALE_ORDER")
                    multiplier = current if current is not None else ONE
                    total += multiplier * m
                    last_major = m
                    current = None
                    small_limit = 100
                flags |= t.flags
                words = words or t.text[:1].isalpha()
                single = None
                consumed += 1
                last_token = t
                after_scale = True
                j += 1
                continue
            if t.type == REL and t.key == "and" and not self.between_open:
                nxt = self._tok(j + 1)
                if (
                    after_scale and small_limit == 100 and nxt is not None
                    and nxt.type == NUMWORD and nxt.attrs.get("lang") == "en"
                    and nxt.attrs.get("kind") in ("unit", "tens")
                ):
                    j += 1
                    continue
                if (
                    current is not None and not after_scale and nxt is not None
                    and nxt.type == NUMWORD and nxt.attrs.get("kind") == "frac"
                    and not nxt.attrs.get("unicode") and nxt.key in _EN_FRACTION_PHRASES
                ):
                    current += nxt.value
                    small_limit = 0
                    single = None
                    j += 2
                    consumed += 1
                    continue
            break
        if consumed == 0:
            return None
        value = total + (current if current is not None else ZERO)
        if article:
            flags.discard("BARE_SCALE")
        elif consumed == 1 and last_token is not None and last_token.type == SCALE:
            flags.add("BARE_SCALE")
        return _Expr(end=j, value=value, flags=flags, words=words, single=single)

    def _digits_group_follows(self, j: int) -> bool:
        """A digit group after a scale must itself carry a smaller scale (``2 crore 50 lakh``)."""
        nxt = self._tok(j + 1)
        return nxt is not None and nxt.type == SCALE

    def _parse_hfrac(self, j: int, start: int):
        t = self.toks[j]
        rule, v = t.attrs["rule"], t.value
        k = j + 1
        number = None
        nxt = self._tok(k)
        if rule != "FIXED" and nxt is not None:
            hindi_unit = (
                nxt.type == NUMWORD
                and nxt.attrs.get("kind") == "unit"
                and nxt.attrs.get("lang") == "hi"
            )
            if nxt.type == NUMBER or hindi_unit:
                number = nxt.value
                k += 1
        if rule == "FIXED":
            base = v
        elif number is not None:
            base = number + v
        elif rule == "ADD_QUARTER":
            base = Decimal("1.25")
        elif rule == "SUB_QUARTER":
            base = Decimal("0.75")
        else:  # साढ़े needs a number
            return None
        scale = ONE
        nxt = self._tok(k)
        if nxt is not None and nxt.type == SCALE:
            scale = nxt.value
            k += 1
        value = base * scale
        expr = _Expr(end=k, value=value, words=True, hfrac=(rule, number, scale != ONE))
        if scale == ONE and value < ONE:
            expr.flags.add("FRACTION_WORD")
            expr.standalone_fraction = True
        return expr

    def _parse_en_fraction(self, j: int, start: int):
        t = self.toks[j]
        k = j + 1
        probe = k
        nxt = self._tok(probe)
        if nxt is not None and nxt.type == WORD and nxt.key == "of":
            probe += 1
            nxt = self._tok(probe)
        if nxt is not None and nxt.type == WORD and nxt.key in ("a", "an"):
            probe += 1
            nxt = self._tok(probe)
        if nxt is not None and nxt.type == SCALE:
            return _Expr(end=probe + 1, value=t.value * nxt.value, words=True,
                         flags=set(nxt.flags))
        if t.key == "quarter":  # "this quarter" is a period; "a quarter" and "three quarters" count
            return None
        expr = _Expr(end=k, value=t.value, words=True)
        expr.flags.add("FRACTION_WORD")
        expr.standalone_fraction = True
        return expr

    # -- times ------------------------------------------------------------------------------
    def _time_after(self, expr: _Expr, start: int):
        """If the expression is followed by a time marker, return (TimeLiteral, end)."""
        mark = self._tok(expr.end)
        if mark is None or mark.type != TIMEMARK:
            return None
        kind = mark.attrs["kind"]
        value = expr.value
        hour = minute = None
        if expr.hfrac is not None:
            rule, number, scaled = expr.hfrac
            if scaled:
                return None
            if rule == "FIXED":
                if value == Decimal("1.5"):
                    hour, minute = 1, 30
                elif value == Decimal("2.5"):
                    hour, minute = 2, 30
            elif number is not None and number == number.to_integral_value():
                n = int(number)
                if rule == "ADD_QUARTER":
                    hour, minute = n, 15
                elif rule == "ADD_HALF":
                    hour, minute = n, 30
                elif rule == "SUB_QUARTER":
                    hour, minute = (n - 1) % 24 if n > 1 else 12, 45
            if hour is None:
                return None
        else:
            if value != value.to_integral_value():
                return None
            hour, minute = int(value), 0
        if kind in ("am", "pm"):
            if not 1 <= hour <= 12:
                return None
            period = "AM" if kind == "am" else "PM"
        else:
            if not 0 <= hour <= 24:
                return None
            period = None
        end = expr.end + 1
        return TimeLiteral(start, end, self._text(start, end), self._orig(start, end),
                           hour, minute, period), end

    # -- main loop --------------------------------------------------------------------------
    def run(self) -> NumericResult:
        i = 0
        while i < self.n:
            t = self.toks[i]
            if t.type == SENT_END:
                self.between_open = False
            elif t.type == REL:
                role = t.attrs.get("role")
                if role == "BETWEEN":
                    self.between_open = True
                elif role == "RANGE_AND":
                    self.between_open = False
            if t.type == TIME:
                i = self._clock(i)
                continue
            if t.type == VAGUE:
                order = t.attrs.get("order")
                self.items.append(Vague(i, i + 1, t.text, t.orig, order, set(t.flags)))
                i += 1
                continue
            starts_expr = t.type in (NUMBER, NUMWORD, SCALE) or (
                t.type == WORD and t.key in ("a", "an") and self._is_scale(self._tok(i + 1))
            )
            if not starts_expr:
                i += 1
                continue
            expr = self._parse_expr(i)
            if expr is None:
                i += 1
                continue
            if expr.error:
                self.errors.append(NumericError(expr.error, i, expr.end, self._text(i, expr.end)))
                i = expr.end
                continue
            i = self._finish(i, expr)
        self._attach_currency()
        self._final_flags()
        flags = set(self.res.flags)
        for tok in self.toks:
            flags |= tok.flags
        for item in self.items:
            flags |= item.flags
        return NumericResult(self.items, self.errors, flags)

    def _clock(self, i: int) -> int:
        t = self.toks[i]
        hour, minute = t.attrs["hour"], t.attrs["minute"]
        end = i + 1
        period = None
        mark = self._tok(end)
        if mark is not None and mark.type == TIMEMARK:
            kind = mark.attrs["kind"]
            if kind in ("am", "pm") and 1 <= hour <= 12:
                period = "AM" if kind == "am" else "PM"
                end += 1
            elif kind == "oclock":
                end += 1
        self.items.append(TimeLiteral(i, end, self._text(i, end), self._orig(i, end),
                                      hour, minute, period))
        return end

    def _finish(self, start: int, expr: _Expr) -> int:
        timed = self._time_after(expr, start)
        if timed is not None:
            literal, end = timed
            self.items.append(literal)
            return end
        end = expr.end
        value = expr.value
        # "one" as a pronoun is not a quantity
        if expr.single is not None and expr.single.key == "one":
            prev, nxt = self._tok(start - 1), self._tok(end)
            if (nxt is not None and nxt.key in _ONE_PRONOUN_AFTER) or (
                prev is not None and prev.key in _ONE_PRONOUN_BEFORE
            ):
                return end
        qtype = "COUNT"
        nxt = self._tok(end)
        if nxt is not None and nxt.type == PERCENT:
            qtype = "PERCENT"
            end += 1
        flags = set(expr.flags)
        for tok in self.toks[start:expr.end]:
            flags |= tok.flags
        if expr.single is not None:
            if expr.single.key in _ONE_WORDS:
                flags.add("WEAK_ONE")
            if expr.single.key == "दो":
                after = self._tok(end)
                if after is None or after.type == SENT_END or (
                    after.type == PUNCT and after.text not in "-–"
                ):
                    flags.add("AMBIGUOUS_NUMWORD")
        self.items.append(
            Quantity(start, end, self._text(start, end), self._orig(start, end), clean(value),
                     qtype, None, flags, expr.words)
        )
        return end

    # -- currency ---------------------------------------------------------------------------
    def _attach_currency(self) -> None:
        quantities = [i for i in self.items if i.kind == "QUANTITY"]
        ends = {q.end: q for q in quantities}
        starts = {q.start: q for q in quantities}
        claimed: dict = {}
        for k, tok in enumerate(self.toks):
            if tok.type != CURRENCY:
                continue
            style, code = tok.attrs["style"], tok.attrs["code"]
            before = ends.get(k)
            after = starts.get(k + 1)
            if before is not None and before.type != "COUNT" and before.type != "MONEY":
                before = None
            if after is not None and after.type not in ("COUNT", "MONEY"):
                after = None

            def free(q):
                return q is not None and id(q) not in claimed

            target = None
            if style == "symbol":
                target = after if free(after) else None
            elif style == "suffix":
                target = before if free(before) else None
            elif style == "code":
                target = after if free(after) else (before if free(before) else None)
            else:
                target = before if free(before) else (after if free(after) else None)
            if target is None:
                for other in (before, after):
                    if other is not None and claimed.get(id(other), code) != code:
                        other.flags.add("CURRENCY_CONFLICT")
                if tok.key in _BARE_CURRENCY_ABBREV:
                    tok.flags.add("NON_QUANTITY")
                continue
            claimed[id(target)] = code
            target.type = "MONEY"
            target.currency = code
            target.flags |= tok.flags
            target.flags.discard("WEAK_ONE")
            if tok.start < self.toks[target.start].start:  # prefix currency
                target.start = k
            else:
                target.end = k + 1
            target.text = self._text(target.start, target.end)
            target.orig = self._orig(target.start, target.end)

    def _final_flags(self) -> None:
        for item in self.items:
            if item.kind == "QUANTITY" and item.type == "PERCENT":
                item.flags.discard("WEAK_ONE")


def assemble(result: LexResult) -> NumericResult:
    return _Assembler(result).run()


def analyze(text: str, lang: str | None = None):
    """Lex and assemble in one call: returns (LexResult, NumericResult)."""
    result = lex(text, lang)
    return result, assemble(result)
