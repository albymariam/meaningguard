"""Lexicon tables and longest-match phrase lookup (spec 01_lexical.md, sections 2, 3, 6).

All Devanagari matching uses the key of rule N5 (nukta removed, chandrabindu -> anusvara) and
Latin matching is case-insensitive (N6). A phrase may only start at a word boundary and may only
end at one (phrases that end in punctuation, such as ``rs.``, skip the end check).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

LEXICON_DIR = Path(__file__).resolve().parent / "lexicons"

NUKTA = "़"
CHANDRABINDU = "ँ"
ANUSVARA = "ं"

# Scale words that only count as a scale when a number precedes them.
NEEDS_NUMBER = {"अरब", "cr", "mn", "bn"}

# Vague quantifiers and the power of ten they stand for (None: small, unspecific).
VAGUE_ORDER = {
    "hundreds": 2,
    "thousands": 3,
    "lakhs of": 5,
    "crores of": 7,
    "millions": 6,
    "billions": 9,
    "dozens": 1,
    "several": None,
    "many": None,
    "few": None,
    "couple": None,
    "सैकड़ों": 2,
    "हज़ारों": 3,
    "हजारों": 3,
    "लाखों": 5,
    "करोड़ों": 7,
    "अरबों": 9,
    "कई": None,
    "कुछ": None,
    "दर्जनों": 1,
}

PERCENT_FORMS = ["%", "percent", "per cent", "प्रतिशत", "फ़ीसदी", "फीसदी"]

# Currency words that can also stand for something else.
AMBIGUOUS_CURRENCY = {"pound", "pounds", "पाउंड", "¥"}

# Currency forms by attachment style (see numerics.py).
CURRENCY_SYMBOLS = {"₹", "$", "us$", "€", "£", "¥"}
CURRENCY_SUFFIX_MARKERS = {"/-"}
CURRENCY_CODES = {"rs", "rs.", "inr", "usd", "eur", "gbp", "jpy", "रू", "रु", "रु."}

# Unicode vulgar fractions that appear in running text.
UNICODE_FRACTIONS = {"½": Decimal("0.5"), "¼": Decimal("0.25"), "¾": Decimal("0.75")}

# Ambiguous English date words: only a date word under the conditions in the matcher.
AMBIGUOUS_DATE_WORDS = {"may", "march", "august", "sat", "sun", "wed", "mar"}


def key_char(ch: str) -> str:
    """Key of one character (N5 for Devanagari, N6 for Latin). May be empty (nukta)."""
    if ch == NUKTA:
        return ""
    if ch == CHANDRABINDU:
        return ANUSVARA
    return ch.casefold()


def make_key(text: str) -> str:
    return "".join(key_char(c) for c in text)


def is_word_char(ch: str) -> bool:
    """Letters and combining marks; Devanagari U+0900-0963 and U+0971-097F. Dandas are not."""
    code = ord(ch)
    if 0x0900 <= code <= 0x097F:
        return code <= 0x0963 or code >= 0x0971
    category = ch.isalpha() or _is_mark(ch)
    return category


def _is_mark(ch: str) -> bool:
    import unicodedata

    return unicodedata.category(ch)[0] == "M"


@dataclass(frozen=True)
class Phrase:
    key: str
    display: str
    type: str
    attrs: tuple = ()  # sorted (name, value) pairs so Phrase stays hashable

    def get(self, name, default=None):
        for k, v in self.attrs:
            if k == name:
                return v
        return default

    def as_dict(self) -> dict:
        return dict(self.attrs)


def _phrase(display: str, type_: str, **attrs) -> Phrase:
    return Phrase(make_key(display), display, type_, tuple(sorted(attrs.items())))


def _load(name: str) -> dict:
    return json.loads((LEXICON_DIR / f"{name}.json").read_text(encoding="utf-8"))


@dataclass
class Lexicon:
    phrases: dict = field(default_factory=dict)  # key -> Phrase
    by_first: dict = field(default_factory=dict)  # first key char -> [Phrase] longest first
    non_negating: list = field(default_factory=list)  # [(keys tuple, role, requires_end)]

    def add(self, phrase: Phrase) -> None:
        if not phrase.key:
            raise ValueError(f"empty lexicon key for {phrase.display!r}")
        existing = self.phrases.get(phrase.key)
        if existing is not None:
            if existing.type == phrase.type and existing.attrs == phrase.attrs:
                return
            raise ValueError(
                f"lexicon key {phrase.key!r} is defined twice: "
                f"{existing.type} {existing.attrs} and {phrase.type} {phrase.attrs}"
            )
        self.phrases[phrase.key] = phrase

    def finish(self) -> None:
        for phrase in self.phrases.values():
            self.by_first.setdefault(phrase.key[0], []).append(phrase)
        for items in self.by_first.values():
            items.sort(key=lambda p: -len(p.key))


def _dec(value) -> Decimal:
    return Decimal(str(value))


@lru_cache(maxsize=1)
def get_lexicon() -> Lexicon:
    lex = Lexicon()

    # --- negation ---
    neg = _load("negation")
    for entry in neg["entries"]:
        for form in entry["forms"]:
            lex.add(
                _phrase(
                    form,
                    "NEG",
                    kind=entry["kind"],
                    lang=entry["lang"],
                    informal=bool(entry.get("informal")),
                )
            )
    for item in neg["non_negating"]:
        if "tokens" in item:
            lex.non_negating.append((tuple(make_key(t) for t in item["tokens"]), item["role"]))

    # --- currency ---
    for entry in _load("currency")["entries"]:
        code = entry["code"]
        for form in entry["symbols"] + entry["words"] + entry.get("suffix_markers", []):
            low = make_key(form)
            if low in CURRENCY_SYMBOLS:
                style = "symbol"
            elif low in CURRENCY_SUFFIX_MARKERS:
                style = "suffix"
            elif low in CURRENCY_CODES:
                style = "code"
            else:
                style = "word"
            lex.add(
                _phrase(
                    form,
                    "CURRENCY",
                    code=code,
                    style=style,
                    ambiguous=low in {make_key(a) for a in AMBIGUOUS_CURRENCY},
                )
            )

    # --- scales ---
    scales = _load("scales")
    for item in scales["scales"]:
        for form in item["forms"]:
            lex.add(
                _phrase(
                    form,
                    "SCALE",
                    value=_dec(item["value"]),
                    exact_unit=False,
                    needs_number=form in NEEDS_NUMBER,
                )
            )
    for item in scales["exact_units"]:
        for form in item["forms"]:
            lex.add(
                _phrase(
                    form, "SCALE", value=_dec(item["value"]), exact_unit=True, needs_number=False
                )
            )
    for lang in ("en", "hi"):
        for form in scales["vague"][lang]:
            lex.add(_phrase(form, "VAGUE", lang=lang, order=VAGUE_ORDER.get(form)))

    # --- relations ---
    for entry in _load("relations")["entries"]:
        for form in entry["forms"]:
            lex.add(
                _phrase(
                    form,
                    "REL",
                    role=entry["role"],
                    lang=entry["lang"],
                    position=entry.get("position", ""),
                )
            )

    # --- calendar ---
    cal = _load("calendar")
    for item in cal["weekdays"]:
        for lang in ("en", "hi"):
            for form in item[lang] + item.get(f"{lang}_plural", []):
                lex.add(
                    _phrase(
                        form,
                        "DAY",
                        id=item["id"],
                        lang=lang,
                        ambiguous=make_key(form) in AMBIGUOUS_DATE_WORDS,
                    )
                )
    for item in cal["months"]:
        for lang in ("en", "hi"):
            for form in item[lang]:
                lex.add(
                    _phrase(
                        form,
                        "MONTH",
                        id=item["id"],
                        lang=lang,
                        ambiguous=make_key(form) in AMBIGUOUS_DATE_WORDS,
                    )
                )
    marks = cal["time_markers"]
    for kind in ("am", "pm", "oclock"):
        for form in marks[kind]:
            lex.add(_phrase(form, "TIMEMARK", kind=kind))
    for part, forms in marks["day_parts"].items():
        for form in forms:
            lex.add(_phrase(form, "DAYPART", part=part))

    # --- number words ---
    en = _load("numbers_en")
    for word, value in en["units"].items():
        lex.add(_phrase(word, "NUMWORD", value=_dec(value), kind="unit", lang="en"))
    for word, value in en["tens"].items():
        lex.add(_phrase(word, "NUMWORD", value=_dec(value), kind="tens", lang="en"))
    for word, value in en["fractions"].items():
        if word == "third":  # "third" is read as an ordinal, never as 1/3
            continue
        lex.add(_phrase(word, "NUMWORD", value=_dec(value), kind="frac", lang="en"))
    hi = _load("numbers_hi")
    for value, forms in hi["numbers"].items():
        if int(value) == 100:  # सौ is a scale; see SCALE above
            continue
        for form in forms:
            lex.add(_phrase(form, "NUMWORD", value=_dec(value), kind="unit", lang="hi"))
    for spec in hi["fractions"].values():
        for form in spec["forms"]:
            attrs = {"kind": "hfrac", "lang": "hi", "rule": spec["rule"]}
            attrs["value"] = _dec(spec["value"]) if "value" in spec else _dec(spec["offset"])
            lex.add(_phrase(form, "NUMWORD", **attrs))
    for form in en["ordinals"] + hi["ordinals"]:
        lex.add(_phrase(form, "ORDINAL"))
    for form, value in UNICODE_FRACTIONS.items():
        lex.add(_phrase(form, "NUMWORD", value=value, kind="frac", lang="en", unicode=True))

    # --- percent ---
    for form in PERCENT_FORMS:
        lex.add(_phrase(form, "PERCENT"))

    lex.finish()
    return lex


def _boundary_before(text: str, i: int, first: str) -> bool:
    if not is_word_char(first):
        return True
    return i == 0 or not is_word_char(text[i - 1])


def _boundary_after(text: str, j: int, last: str) -> bool:
    if not is_word_char(last):
        return True
    return j >= len(text) or not is_word_char(text[j])


def match_at(text: str, i: int, lex: Lexicon | None = None):
    """Longest lexicon phrase starting at text[i]. Returns (Phrase, end index) or None."""
    lex = lex or get_lexicon()
    first = key_char(text[i])
    if not first:
        return None
    for phrase in lex.by_first.get(first[0], ()):
        end = _match_phrase(text, i, phrase.key)
        if end is None:
            continue
        shown_first = phrase.display[0]
        shown_last = phrase.display[-1]
        if not _boundary_before(text, i, shown_first):
            continue
        if not _boundary_after(text, end, shown_last):
            continue
        return phrase, end
    return None


def _match_phrase(text: str, i: int, key: str):
    """Compare ``key`` against the text from i, skipping nukta; returns the end index or None."""
    pos, n = 0, len(text)
    j = i
    while pos < len(key):
        if j >= n:
            return None
        part = key_char(text[j])
        if part == "":
            j += 1
            continue
        if key[pos : pos + len(part)] != part:
            return None
        pos += len(part)
        j += 1
    while j < n and text[j] == NUKTA:
        j += 1
    return j
