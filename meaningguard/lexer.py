"""Lexer for English and Hindi (spec 01_lexical.md).

``lex(text)`` normalizes (N1-N8), splits sentences, and returns typed tokens with offsets that map
back to the original text. Numbers are lexed here (NUM-1 to NUM-5, NUM-10, NUM-11); number words
and scales are assembled into values in ``numerics.py``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from .lexicon import (
    CURRENCY_SYMBOLS,
    get_lexicon,
    is_word_char,
    make_key,
    match_at,
)

MAX_CHARS = 1000

ABBREVIATIONS = {
    "rs", "no", "a.m.", "p.m.", "mr", "mrs", "dr", "st", "approx", "vs", "etc", "inr",
    "ms", "prof", "jr", "sr",
}
SENTENCE_STARTER_SYMBOLS = set(CURRENCY_SYMBOLS)

# Token types
NUMBER, NUMWORD, SCALE, VAGUE, CURRENCY, PERCENT = (
    "NUMBER", "NUMWORD", "SCALE", "VAGUE", "CURRENCY", "PERCENT",
)
NEG, REL, DAY, MONTH, TIMEMARK, DAYPART = "NEG", "REL", "DAY", "MONTH", "TIMEMARK", "DAYPART"
TIME, WORD, PUNCT, SENT_END = "TIME", "WORD", "PUNCT", "SENT_END"


class LexError(ValueError):
    code = "LEX_ERROR"


class EmptyInput(LexError):
    code = "EmptyInput"


class InputTooLong(LexError):
    code = "InputTooLong"


# ---------------------------------------------------------------------------------------------
# Normalization (N1-N4). N5/N6 are applied at match time (lexicon.make_key / key_char).

_DELETE = {"​", "‌", "‍", "﻿", "­"}
_MAP = {
    "‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-",
}


@dataclass
class Normalized:
    text: str
    ostart: list = field(default_factory=list)
    oend: list = field(default_factory=list)

    def span(self, a: int, b: int) -> tuple:
        """Original-text offsets of normalized[a:b]."""
        if b <= a:
            return (self.ostart[a], self.ostart[a]) if a < len(self.ostart) else (0, 0)
        return (self.ostart[a], self.oend[b - 1])


def normalize(text: str) -> Normalized:
    out, ostart, oend = [], [], []
    i, n = 0, len(text)
    while i < n:
        j = i + 1
        while j < n and unicodedata.combining(text[j]):
            j += 1
        for ch in unicodedata.normalize("NFC", text[i:j]):  # N1
            if ch in _DELETE:  # N2
                continue
            ch = _MAP.get(ch, ch)  # N3
            if ch.isspace() or unicodedata.category(ch) == "Zs":  # N4
                if not out or out[-1] == " ":
                    if out:
                        oend[-1] = j
                    continue
                ch = " "
            out.append(ch)
            ostart.append(i)
            oend.append(j)
        i = j
    while out and out[-1] == " ":
        out.pop()
        ostart.pop()
        oend.pop()
    return Normalized("".join(out), ostart, oend)


# ---------------------------------------------------------------------------------------------
# Tokens

@dataclass
class Token:
    type: str
    text: str
    start: int
    end: int
    orig: tuple
    sentence: int
    value: Decimal | None = None
    flags: set = field(default_factory=set)
    attrs: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return make_key(self.text)

    def __repr__(self) -> str:  # compact, for test failures
        extra = f" {self.value}" if self.value is not None else ""
        flags = f" {sorted(self.flags)}" if self.flags else ""
        return f"<{self.type} {self.text!r}{extra}{flags}>"


@dataclass
class LexResult:
    source: str
    norm: Normalized
    tokens: list
    sentence_count: int
    flags: set

    @property
    def text(self) -> str:
        return self.norm.text

    @property
    def neg_count(self) -> int:
        return sum(1 for t in self.tokens if t.type == NEG and t.attrs.get("counts"))

    def of_type(self, *types: str) -> list:
        return [t for t in self.tokens if t.type in types]


# ---------------------------------------------------------------------------------------------
# Number patterns

_RE_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_RE_NUM_DATE = re.compile(r"(?<!\d)\d{1,2}[/-]\d{1,2}[/-]\d{2,4}(?!\d)")
_RE_VERSION = re.compile(r"(?<!\d)\d+(?:\.\d+){2,}(?![\d])")
_RE_PLUS_PHONE = re.compile(r"\+\d{1,3}[ -]\d{5,}(?:[ -]\d{2,6})*(?!\d)")
_RE_BARE_PHONE = re.compile(r"(?<![\d,.])\d{10,}(?![,.]\d)")
_RE_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)")
_RE_NUMBER = re.compile(
    r"(?P<int>\d{1,3}(?:,\d{3})+(?!\d)|\d{1,2}(?:,\d{2})*,\d{3}(?!\d)|\d+)(?P<frac>\.\d+)?"
)
_RE_COMMA_DEC = re.compile(r",(\d{1,2})(?!\d)")
_RE_ORD_EN = re.compile(r"(?:st|nd|rd|th)(?![A-Za-z0-9])", re.IGNORECASE)
_RE_ORD_HI = re.compile(r"(?:वाँ|वां|वीं|वें|वा|वी|वे)(?![ऀ-ॣॱ-ॿ])")
_RE_ATTACHED = re.compile(r"(cr|mn|bn|k|l|m|b)(?![A-Za-z])", re.IGNORECASE)
_RE_IDENT = re.compile(r"(?:[A-Za-z]+\d[A-Za-z0-9]*|[A-Z]{2,}-\d[A-Za-z0-9]*)")
_RE_NO_ABBREV = re.compile(r"[Nn][Oo]\.\s?(?=\d)")

_ATTACHED_SCALE = {
    "k": (Decimal(1000), False), "l": (Decimal(100000), True), "cr": (Decimal(10000000), False),
    "m": (Decimal(1000000), True), "mn": (Decimal(1000000), False),
    "b": (Decimal(1000000000), True), "bn": (Decimal(1000000000), False),
}
_IDENT_EXEMPT = None  # filled lazily from the lexicon

_DATE_PRECEDERS = {
    "in", "of", "on", "by", "since", "from", "until", "till", "before", "after", "during",
    "this", "next", "last",
}


def _is_digit(ch: str) -> bool:
    return unicodedata.category(ch) == "Nd"


def digits_to_ascii(text: str) -> str:
    return "".join(str(unicodedata.digit(c)) if _is_digit(c) else c for c in text)


def _digit_scripts(text: str) -> set:
    scripts = set()
    for ch in text:
        if _is_digit(ch):
            scripts.add("ASCII" if ord(ch) < 128 else unicodedata.name(ch).split()[0])
    return scripts


def _is_latin_letter(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def _is_devanagari_letter(ch: str) -> bool:
    return 0x0904 <= ord(ch) <= 0x0939 or 0x0958 <= ord(ch) <= 0x0961 or 0x0971 <= ord(ch) <= 0x097F


def _ident_exempt() -> set:
    global _IDENT_EXEMPT
    if _IDENT_EXEMPT is None:
        lex = get_lexicon()
        exempt = set()
        for phrase in lex.phrases.values():
            if phrase.type in ("CURRENCY", "MONTH", "DAY", "TIMEMARK") and phrase.key.isascii():
                exempt.add(phrase.key.rstrip("."))
        _IDENT_EXEMPT = exempt
    return _IDENT_EXEMPT


# ---------------------------------------------------------------------------------------------
# Scanner

class _Scanner:
    def __init__(self, norm: Normalized):
        self.norm = norm
        self.T = norm.text
        self.n = len(self.T)
        self.tokens: list = []
        self.sentence = 0
        self.lex = get_lexicon()
        self.force_nonquantity = False
        self.comma_suspect: set = set()
        self.sign_at = -1

    # -- emit helpers -----------------------------------------------------------------------
    def emit(self, type_, a, b, value=None, flags=(), **attrs) -> Token:
        token = Token(
            type_, self.T[a:b], a, b, self.norm.span(a, b), self.sentence, value, set(flags),
            dict(attrs),
        )
        self.tokens.append(token)
        return token

    def run(self) -> list:
        T, n, i = self.T, self.n, 0
        while i < n:
            if T[i] == " ":
                i += 1
                continue
            for step in (self._sentence_end, self._number, self._ident, self._phrase,
                         self._word, self._punct):
                j = step(i)
                if j is not None:
                    i = j
                    break
        return self.tokens

    # -- sentence ends ----------------------------------------------------------------------
    def _sentence_end(self, i):
        T, n = self.T, self.n
        if T[i] in "?!।॥":
            j = i + 1
            while j < n and T[j] in "?!।॥":
                j += 1
            return self._close_sentence(i, j)
        if T[i] == ".":
            if i + 1 < n and T[i + 1] == ".":
                return None
            if self._dot_ends_sentence(i):
                return self._close_sentence(i, i + 1)
        return None

    def _close_sentence(self, a, b):
        self.emit(SENT_END, a, b)
        T, n = self.T, self.n
        while b < n and T[b] in "\"')]}":
            self.emit(PUNCT, b, b + 1)
            b += 1
        self.sentence += 1
        return b

    def _dot_ends_sentence(self, i) -> bool:
        T, n = self.T, self.n
        j = i + 1
        if j >= n:
            return True
        while j < n and T[j] in "\"')]}":
            j += 1
        if j >= n:
            return True
        if T[j] != " ":
            return False
        k = j
        while k < n and T[k] == " ":
            k += 1
        while k < n and T[k] in "\"'([{":
            k += 1
        if k >= n:
            return True
        c = T[k]
        starter = (
            c.isupper() or _is_digit(c) or _is_devanagari_letter(c) or c in SENTENCE_STARTER_SYMBOLS
        )
        if not starter:
            return False
        prev = self.tokens[-1] if self.tokens else None
        if prev is not None and prev.end == i and prev.sentence == self.sentence:
            word = prev.text.casefold()
            if word in ABBREVIATIONS:
                return word == "no" and not _is_digit(c)
            if prev.type == WORD and len(prev.text) == 1 and prev.text.isupper():
                return False  # an initial such as "A."
        return True

    # -- numbers ----------------------------------------------------------------------------
    def _opaque(self, a, b, kind, **attrs):
        token = self.emit(WORD, a, b, flags={"NON_QUANTITY"}, opaque=kind, **attrs)
        token.attrs["digits"] = digits_to_ascii(token.text)
        return b

    def _number(self, i):
        T, n = self.T, self.n
        ch = T[i]
        if ch == "+":
            m = _RE_PLUS_PHONE.match(T, i)
            return self._opaque(i, m.end(), "PHONE") if m else None
        if ch == "." and i + 1 < n and _is_digit(T[i + 1]) and (
            i == 0 or not (is_word_char(T[i - 1]) or _is_digit(T[i - 1]) or T[i - 1] == ".")
        ):
            return self._leading_decimal(i)
        if not _is_digit(ch):
            return None
        for rx, kind in ((_RE_ISO_DATE, "DATE"), (_RE_NUM_DATE, "DATE"), (_RE_VERSION, "VERSION")):
            m = rx.match(T, i)
            if m:
                return self._opaque(i, m.end(), kind)
        m = _RE_TIME.match(T, i)
        if m:
            hour, minute = int(digits_to_ascii(m.group(1))), int(digits_to_ascii(m.group(2)))
            second = int(digits_to_ascii(m.group(3))) if m.group(3) else None
            if hour <= 24 and minute <= 59 and (second is None or second <= 59):
                self.emit(TIME, i, m.end(), hour=hour, minute=minute, second=second)
                return m.end()
        m = _RE_BARE_PHONE.match(T, i)
        if m:
            return self._opaque(i, m.end(), "PHONE")
        m = _RE_NUMBER.match(T, i)
        j = m.end()
        int_part, frac = m.group("int"), m.group("frac")
        flags = set()
        scripts = _digit_scripts(m.group(0))
        if len(scripts) > 1:
            flags.add("MIXED_DIGIT_SCRIPT")
        if frac and len(frac) == 4 and "," not in int_part and len(int_part) <= 3:
            flags.add("DOT_THOUSANDS_SUSPECT")
        comma = _RE_COMMA_DEC.match(T, j)
        if comma:
            flags.add("COMMA_DECIMAL_SUSPECT")
            self.comma_suspect.add(comma.start(1))
        if i in self.comma_suspect:
            flags.add("COMMA_DECIMAL_SUSPECT")
        value = Decimal(digits_to_ascii(m.group(0)).replace(",", ""))
        negative = self.sign_at == i
        if negative:
            value = -value
        if self.force_nonquantity:
            self.force_nonquantity = False
            return self._opaque(i, j, "NUMBER_ABBREV")
        if j < n:
            c = T[j]
            if _is_latin_letter(c):
                ordinal = _RE_ORD_EN.match(T, j)
                if ordinal and not frac:
                    return self._opaque(i, ordinal.end(), "ORDINAL")
                attached = _RE_ATTACHED.match(T, j)
                if attached:
                    self._emit_number(i, j, value, flags, negative)
                    word = attached.group(1).casefold()
                    scale, ambiguous = _ATTACHED_SCALE[word]
                    sflags = {"AMBIGUOUS_ABBREV"} if ambiguous else set()
                    self.emit(SCALE, j, attached.end(), scale, sflags, attached=True,
                              exact_unit=False, needs_number=False)
                    return attached.end()
            elif _is_devanagari_letter(c) and not frac:
                ordinal = _RE_ORD_HI.match(T, j)
                if ordinal:
                    return self._opaque(i, ordinal.end(), "ORDINAL")
        self._emit_number(i, j, value, flags, negative)
        return j

    def _emit_number(self, a, b, value, flags, negative):
        if negative:
            self.emit(NUMBER, a - 1, b, value, flags | {"NEGATIVE"})
        else:
            self.emit(NUMBER, a, b, value, flags)

    def _leading_decimal(self, i):
        m = re.compile(r"\.\d+").match(self.T, i)
        value = Decimal("0" + digits_to_ascii(m.group(0)))
        self.emit(NUMBER, i, m.end(), value, flags=set())
        return m.end()

    # -- identifiers like A5, H2O, COVID-19 ----------------------------------------------------
    def _ident(self, i):
        T = self.T
        if not _is_latin_letter(T[i]) or (i > 0 and is_word_char(T[i - 1])):
            return None
        m = _RE_IDENT.match(T, i)
        if not m:
            return None
        letters = re.match(r"[A-Za-z]+", m.group(0)).group(0)
        if letters.casefold() in _ident_exempt():
            return None
        return self._opaque(i, m.end(), "IDENT")

    # -- lexicon phrases --------------------------------------------------------------------
    def _phrase(self, i):
        T, n = self.T, self.n
        if _RE_NO_ABBREV.match(T, i) and (i == 0 or not is_word_char(T[i - 1])):
            end = i + 3  # "No."
            self.emit(WORD, i, end, flags={"NON_QUANTITY"}, opaque="NUMBER_ABBREV")
            self.force_nonquantity = True
            return end
        found = match_at(T, i, self.lex)
        if not found:
            return None
        phrase, end = found
        type_ = phrase.type
        if (
            type_ not in ("NUMWORD", "SCALE", "VAGUE")
            and end + 1 < n
            and T[end] == "-"
            and is_word_char(T[end + 1])
            and is_word_char(T[end - 1])
        ):
            return None  # part of a hyphenated word such as "non-refundable"
        if type_ in ("DAY", "MONTH") and phrase.get("ambiguous") and not self._date_word_ok(i, end):
            return None
        attrs = phrase.as_dict()
        if type_ == "NEG":
            flags = {"INFORMAL_NEG"} if attrs.pop("informal", False) else set()
            self.emit(NEG, i, end, flags=flags, **attrs)
        elif type_ == "REL":
            token = self.emit(REL, i, end, **attrs)
            self._maybe_alt_split(token, phrase)
        elif type_ == "CURRENCY":
            is_word_form = attrs.get("style") == "word"
            if attrs.get("ambiguous") and is_word_form and self._followed_by_of(end):
                return None  # "5 pounds of rice": a weight, not money
            flags = {"AMBIGUOUS_CURRENCY"} if attrs.pop("ambiguous", False) else set()
            self.emit(CURRENCY, i, end, flags=flags, **attrs)
        elif type_ in ("SCALE", "NUMWORD"):
            value = attrs.pop("value")
            self.emit(type_, i, end, value=value, **attrs)
        elif type_ == "ORDINAL":
            self.emit(WORD, i, end, flags={"NON_QUANTITY"}, opaque="ORDINAL")
        else:
            attrs.pop("ambiguous", None)
            self.emit(type_, i, end, **attrs)
        return end

    def _followed_by_of(self, end) -> bool:
        T = self.T
        k = end
        while k < self.n and T[k] == " ":
            k += 1
        return T[k : k + 2].casefold() == "of" and (k + 2 >= self.n or not is_word_char(T[k + 2]))

    def _maybe_alt_split(self, token, phrase):
        """``से बढ़कर`` is GT unless a value follows; record the alternative reading."""
        if " " not in phrase.display or not phrase.display.startswith("से "):
            return
        rest = self.lex.phrases.get(make_key(phrase.display[3:]))
        if rest is not None and rest.type == "REL" and rest.get("role", "").endswith("_PART"):
            token.attrs["alt_split"] = (("FROM_OR_THAN", "से"), (rest.get("role"), rest.display))

    def _date_word_ok(self, i, end) -> bool:
        T, n = self.T, self.n
        text = T[i:end]
        k = end
        while k < n and T[k] == " ":
            k += 1
        if k < n and _is_digit(T[k]):
            return True
        prev = self.tokens[-1] if self.tokens else None
        in_sentence = prev is not None and prev.sentence == self.sentence
        if in_sentence and prev.text.casefold() in _DATE_PRECEDERS:
            return True
        return text[0].isupper() and in_sentence

    # -- words and punctuation --------------------------------------------------------------
    def _word(self, i):
        T, n = self.T, self.n
        if not is_word_char(T[i]):
            return None
        j = i
        while j < n:
            c = T[j]
            if is_word_char(c):
                j += 1
            elif (
                c in "'-" and j > i and j + 1 < n
                and is_word_char(T[j - 1]) and is_word_char(T[j + 1])
            ):
                j += 1
            else:
                break
        text = T[i:j]
        if any("ऀ" <= c <= "ॿ" for c in text):
            script = "devanagari"
        elif any(_is_latin_letter(c) for c in text):
            script = "latin"
        else:
            script = "other"
        self.emit(WORD, i, j, script=script)
        return j

    def _punct(self, i):
        T, n = self.T, self.n
        if (
            T[i] == "-" and i + 1 < n and _is_digit(T[i + 1])
            and (i == 0 or not (is_word_char(T[i - 1]) or _is_digit(T[i - 1]) or T[i - 1] in "-/."))
        ):
            self.sign_at = i + 1  # a minus sign directly before a number belongs to the number
            return i + 1
        self.emit(PUNCT, i, i + 1)
        return i + 1


# ---------------------------------------------------------------------------------------------
# Post passes

def _demote_bare_scales(tokens: list) -> None:
    """अरब, cr, mn, bn are scales only after a number."""
    for idx, tok in enumerate(tokens):
        if tok.type == SCALE and tok.attrs.get("needs_number"):
            prev = tokens[idx - 1] if idx else None
            if not (prev and prev.sentence == tok.sentence and prev.type in (NUMBER, NUMWORD)):
                tok.type = WORD
                tok.value = None
                tok.attrs = {"script": "devanagari" if "ऀ" <= tok.text[0] <= "ॿ" else "latin"}


def _annotate_negation(tokens: list) -> None:
    lex = get_lexicon()
    by_sentence: dict = {}
    for tok in tokens:
        by_sentence.setdefault(tok.sentence, []).append(tok)
    for sent in by_sentence.values():
        for tok in sent:
            if tok.type == NEG:
                tok.attrs["counts"] = tok.attrs["kind"] != "AMBIG"
                if not tok.attrs["counts"]:
                    tok.attrs["role"] = "AMBIG"
        keys = [t.key for t in sent]
        for seq, role in lex.non_negating:
            size = len(seq)
            for k in range(len(sent) - size + 1):
                if tuple(keys[k : k + size]) != seq:
                    continue
                if role == "TAG_QUESTION":
                    after = sent[k + size] if k + size < len(sent) else None
                    if after is not None and after.type != SENT_END:
                        continue
                for tok in sent[k : k + size]:
                    if tok.type == NEG:
                        tok.attrs["counts"] = False
                        tok.attrs["role"] = role
        seen_corr = False
        seen_plain = False
        for tok in sent:
            if tok.type != NEG or not tok.attrs["counts"]:
                continue
            if tok.attrs["kind"] == "NEG_CORR":
                if seen_corr or (tok.key == "nor" and seen_plain):
                    tok.attrs["counts"] = False
                    tok.attrs["role"] = "CORRELATIVE"
                seen_corr = True
            else:
                seen_plain = True


def _script_flags(tokens: list, lang: str | None) -> set:
    flags = set()
    latin = any(t.type == WORD and t.attrs.get("script") == "latin" for t in tokens)
    devanagari = any(t.type == WORD and t.attrs.get("script") == "devanagari" for t in tokens)
    if latin and devanagari:
        flags.add("MIXED_SCRIPT")
    if lang == "hi":
        for tok in tokens:
            if tok.type == WORD and tok.attrs.get("script") == "latin" and len(tok.text) >= 3:
                if not tok.flags & {"NON_QUANTITY"}:
                    tok.flags.add("UNTRANSLATED_LATIN")
                    flags.add("UNTRANSLATED_LATIN")
    return flags


def lex(text: str, lang: str | None = None) -> LexResult:
    """Lex one input (1 to 1000 characters). ``lang`` ('en' or 'hi') only drives
    UNTRANSLATED_LATIN; matching is by script."""
    if len(text) > MAX_CHARS:
        raise InputTooLong(f"input has {len(text)} characters, the limit is {MAX_CHARS}")
    norm = normalize(text)
    if not norm.text:
        raise EmptyInput("input is empty")
    tokens = _Scanner(norm).run()
    _demote_bare_scales(tokens)
    _annotate_negation(tokens)
    flags = _script_flags(tokens, lang)
    for tok in tokens:
        flags |= tok.flags
    sentences = (max(t.sentence for t in tokens) + 1) if tokens else 0
    return LexResult(text, norm, tokens, sentences, flags)
