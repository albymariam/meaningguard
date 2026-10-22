"""Integrity tests for the lexicon data files (Phase 1)."""

import json
import unicodedata
from pathlib import Path

import pytest

LEX = Path(__file__).resolve().parent.parent / "meaningguard" / "lexicons"
FILES = [
    "negation", "currency", "scales", "relations", "calendar", "numbers_en", "numbers_hi",
]
NUKTA = "़"
CHANDRABINDU = "ँ"
ANUSVARA = "ं"


def load(name):
    return json.loads((LEX / f"{name}.json").read_text(encoding="utf-8"))


def key(text):
    """Lexicon match key (spec 01_lexical.md N5/N6)."""
    text = unicodedata.normalize("NFC", text).casefold()
    return text.replace(NUKTA, "").replace(CHANDRABINDU, ANUSVARA)


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from walk_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_strings(value)


@pytest.mark.parametrize("name", FILES)
def test_file_loads_and_has_version(name):
    assert load(name)["version"] == 1


@pytest.mark.parametrize("name", FILES)
def test_all_strings_are_nfc(name):
    for text in walk_strings(load(name)):
        assert unicodedata.normalize("NFC", text) == text, text


@pytest.mark.parametrize("name", FILES)
def test_no_zero_width_or_stray_spaces(name):
    bad = {"​", "‌", "‍", "﻿", "­", " "}
    for text in walk_strings(load(name)):
        assert not (set(text) & bad), repr(text)


def test_hindi_numbers_cover_zero_to_hundred_exactly():
    numbers = load("numbers_hi")["numbers"]
    assert sorted(map(int, numbers)) == list(range(101))
    for value, forms in numbers.items():
        assert forms and all(isinstance(f, str) and f for f in forms), value


def test_hindi_number_forms_do_not_collide_across_values():
    seen = {}
    for value, forms in load("numbers_hi")["numbers"].items():
        for form in forms:
            owner = seen.setdefault(key(form), value)
            assert owner == value, f"{form!r} used for {owner} and {value}"


def test_hindi_number_forms_do_not_collide_with_other_categories():
    numbers = {key(f): v for v, fs in load("numbers_hi")["numbers"].items() for f in fs}
    other = set()
    for name in ("negation", "scales", "currency"):
        for entry in load(name).get("entries", load(name).get("scales", [])):
            for form in entry.get("forms", []) + entry.get("words", []):
                other.add(key(form))
    clash = {k for k in numbers if k in other and k not in {key("सौ")}}
    assert not clash, clash


def test_scales_are_powers_and_unique():
    scales = load("scales")["scales"]
    values = [s["value"] for s in scales]
    assert values == sorted(values) and len(values) == len(set(values))
    seen = {}
    for scale in scales:
        for form in scale["forms"]:
            assert seen.setdefault(key(form), scale["value"]) == scale["value"], form


def test_scale_key_merges_are_intended():
    forms = {key(f) for s in load("scales")["scales"] for f in s["forms"]}
    assert key("हज़ार") == key("हजार") and key("करोड़") == key("करोड")
    assert key("हज़ार") in forms and key("करोड़") in forms


def test_negation_entries_have_known_kinds_and_langs():
    data = load("negation")
    for entry in data["entries"]:
        assert entry["kind"] in data["kinds"], entry
        assert entry["lang"] in {"en", "hi"}
        assert entry["forms"]


def test_kabhi_alone_is_not_a_negator():
    forms = {key(f) for e in load("negation")["entries"] for f in e["forms"]}
    assert key("कभी") not in forms
    assert key("कभी नहीं") in forms


def test_no_negation_form_listed_twice_with_different_kinds():
    seen = {}
    for entry in load("negation")["entries"]:
        for form in entry["forms"]:
            assert seen.setdefault((entry["lang"], key(form)), entry["kind"]) == entry["kind"], form


def test_currency_codes_unique_and_nonempty():
    codes = [e["code"] for e in load("currency")["entries"]]
    assert len(codes) == len(set(codes)) and "INR" in codes
    owners = {}
    for entry in load("currency")["entries"]:
        for form in entry["symbols"] + entry["words"]:
            assert owners.setdefault(key(form), entry["code"]) == entry["code"], form


def test_relation_roles_have_languages_and_forms():
    for entry in load("relations")["entries"]:
        assert entry["lang"] in {"en", "hi"} and entry["forms"] and entry["role"]


def test_gt_gte_lt_lte_exist_in_both_languages():
    roles = {(e["role"], e["lang"]) for e in load("relations")["entries"]}
    for role in ("GT", "GTE", "LT", "LTE", "FROM_OR_THAN", "TIME_BEFORE", "TIME_AFTER"):
        langs = {lang for r, lang in roles if r == role}
        assert langs, role
    for role in ("GT", "GTE", "LT", "LTE", "TIME_BEFORE", "TIME_AFTER", "COND_IF", "COND_ELSE"):
        assert {"en", "hi"} <= {lang for r, lang in roles if r == role}, role


def test_calendar_complete():
    data = load("calendar")
    assert [d["id"] for d in data["weekdays"]] == list(range(7))
    assert [m["id"] for m in data["months"]] == list(range(1, 13))
    for item in data["weekdays"] + data["months"]:
        assert item["en"] and item["hi"]


def test_english_numbers_cover_zero_to_nineteen_and_tens():
    data = load("numbers_en")
    assert sorted(data["units"].values()) == list(range(20))
    assert sorted(set(data["tens"].values())) == [20, 30, 40, 50, 60, 70, 80, 90]
