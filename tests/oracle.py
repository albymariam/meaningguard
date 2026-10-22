"""Independent reference evaluator for number-word rules (spec 01 NUM-7/NUM-8, 02 section 4).

It exists only to check the spec's worked examples. It deliberately shares no code with the
lexer that Phase 3 will write, so the two can be compared against each other later.
"""

from __future__ import annotations

import json
import unicodedata
from fractions import Fraction
from pathlib import Path

LEX = Path(__file__).resolve().parent.parent / "meaningguard" / "lexicons"


def key(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return text.replace("़", "").replace("ँ", "ं")


def _load(name):
    return json.loads((LEX / f"{name}.json").read_text(encoding="utf-8"))


def _tables():
    hi = _load("numbers_hi")
    en = _load("numbers_en")
    scales = _load("scales")["scales"]
    hi_nums = {key(f): int(v) for v, forms in hi["numbers"].items() for f in forms}
    hi_frac = {}
    for spec in hi["fractions"].values():
        for form in spec["forms"]:
            hi_frac[key(form)] = spec
    scale = {key(f): Fraction(s["value"]) for s in scales for f in s["forms"]}
    en_nums = {**en["units"], **en["tens"]}
    return hi_nums, hi_frac, scale, en_nums, en["fractions"]


HI_NUMS, HI_FRAC, SCALE, EN_NUMS, EN_FRAC = _tables()


def eval_hindi(phrase: str) -> Fraction:
    tokens = [key(t) for t in phrase.split()]
    total, current, i = Fraction(0), None, 0
    while i < len(tokens):
        word = tokens[i]
        if word in HI_FRAC:
            spec = HI_FRAC[word]
            i += 1
            number = None
            if (
                spec["rule"] != "FIXED"
                and i < len(tokens)
                and tokens[i] in HI_NUMS
                and tokens[i] not in SCALE  # सौ right after a fraction word is the scale M, never N
            ):
                number = Fraction(HI_NUMS[tokens[i]])
                i += 1
            if spec["rule"] == "FIXED":
                base = Fraction(str(spec["value"]))
            elif number is not None:
                base = number + Fraction(str(spec["offset"]))
            elif spec["rule"] == "ADD_QUARTER":
                base = Fraction(1) + Fraction(str(spec["offset"]))
            else:
                raise ValueError(f"{word} needs a number")
            if i < len(tokens) and tokens[i] in SCALE:
                base *= SCALE[tokens[i]]
                i += 1
            total += base
            continue
        if word in SCALE and (word != key("सौ") or current is not None):
            current = (current if current is not None else Fraction(1)) * SCALE[word]
            if SCALE[word] >= 1000:
                total += current
                current = None
        elif word in HI_NUMS:
            value = HI_NUMS[word]
            if value == 100:
                current = (current if current is not None else Fraction(1)) * 100
            else:
                current = (current if current is not None else Fraction(0)) + value
        else:
            raise ValueError(f"unknown word {word!r}")
        i += 1
    return total + (current if current is not None else 0)


def eval_english(phrase: str) -> Fraction:
    tokens = phrase.lower().replace("-", " ").split()
    total, current, pending, i = Fraction(0), None, None, 0
    while i < len(tokens):
        word = tokens[i]
        if word in {"and", "a", "of"}:
            i += 1
            continue
        pair = " ".join(tokens[i : i + 2])
        if pair in EN_FRAC:  # multi-word fractions such as "three quarters"
            pending = Fraction(str(EN_FRAC[pair]))
            i += 2
            continue
        if word in EN_FRAC:
            pending = Fraction(str(EN_FRAC[word]))
        elif word in EN_NUMS:
            current = (current or Fraction(0)) + EN_NUMS[word]
        elif word == "hundred":
            current = (current if current is not None else Fraction(1)) * 100
        elif word in SCALE:
            if pending is not None:
                base = pending
            else:
                base = current if current is not None else Fraction(1)
            total += base * SCALE[word]
            current, pending = None, None
        else:
            raise ValueError(f"unknown word {word!r}")
        i += 1
    return total + (current or 0) + (pending or 0)  # a bare fraction such as "three quarters"
