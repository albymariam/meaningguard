"""Number assembly: property tests against an independent generator, plus an edge-case table."""

import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meaningguard.numerics import analyze
from tests.oracle import eval_english, eval_hindi

LEX = Path(__file__).resolve().parent.parent / "meaningguard" / "lexicons"
HI = json.loads((LEX / "numbers_hi.json").read_text(encoding="utf-8"))["numbers"]
HI_WORD = {int(v): forms[0] for v, forms in HI.items()}

EN_UNITS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
EN_TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}


def en_under_100(n, hyphen):
    if n < 20:
        return EN_UNITS[n]
    tens, unit = divmod(n, 10)
    word = EN_TENS[tens * 10]
    return word if unit == 0 else word + ("-" if hyphen else " ") + EN_UNITS[unit]


def en_under_1000(n, hyphen, use_and):
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(EN_UNITS[hundreds] + " hundred")
    if rest:
        if hundreds and use_and:
            parts.append("and")
        parts.append(en_under_100(rest, hyphen))
    return " ".join(parts)


def en_words(n, hyphen=True, use_and=True):
    """Western scales: billion, million, thousand."""
    if n == 0:
        return "zero"
    parts = []
    for scale, name in ((10**9, "billion"), (10**6, "million"), (10**3, "thousand")):
        chunk, n = divmod(n, scale)
        if chunk:
            parts.append(en_under_1000(chunk, hyphen, use_and) + " " + name)
    if n:
        if parts and use_and and n < 100:
            parts.append("and")
        parts.append(en_under_1000(n, hyphen, use_and))
    return " ".join(parts)


def hi_words(n):
    """Indian scales: करोड़, लाख, हज़ार, सौ with atomic words below 100."""
    if n == 0:
        return HI_WORD[0]
    parts = []
    for scale, name in ((10**7, "करोड़"), (10**5, "लाख"), (10**3, "हज़ार")):
        chunk, n = divmod(n, scale)
        if chunk:
            parts.append(hi_words(chunk) + " " + name)
    hundreds, rest = divmod(n, 100)
    if hundreds:
        parts.append(HI_WORD[hundreds] + " सौ")
    if rest:
        parts.append(HI_WORD[rest])
    return " ".join(parts)


def single_value(text):
    _, numeric = analyze(text)
    assert not numeric.errors, text
    assert len(numeric.quantities) == 1, (text, numeric.quantities)
    return numeric.quantities[0].value


@settings(max_examples=400, deadline=None)
@given(st.integers(min_value=0, max_value=999_999_999), st.booleans(), st.booleans())
def test_english_words_round_trip(n, hyphen, use_and):
    assert single_value(en_words(n, hyphen, use_and)) == n


@settings(max_examples=400, deadline=None)
@given(st.integers(min_value=0, max_value=999_999_999))
def test_hindi_words_round_trip(n):
    assert single_value(hi_words(n)) == n


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=1, max_value=999_999_999))
def test_digits_with_all_groupings_agree(n):
    western = f"{n:,}"
    indian = _indian(n)
    assert single_value(western) == n
    assert single_value(indian) == n
    assert single_value(str(n) if n < 10**9 else western) == n


def _indian(n):
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


@settings(max_examples=150, deadline=None)
@given(st.integers(min_value=1, max_value=99), st.sampled_from([100, 1000, 100000, 10000000]))
def test_hindi_fraction_words_match_oracle(n, scale):
    names = {100: "सौ", 1000: "हज़ार", 100000: "लाख", 10000000: "करोड़"}
    for prefix in ("सवा", "साढ़े", "पौने"):
        if prefix == "पौने" and n == 1:
            continue
        phrase = f"{prefix} {HI_WORD[n]} {names[scale]}"
        assert Fraction(single_value(phrase)) == eval_hindi(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "सवा लाख",
        "सवा सौ",
        "सवा तीन",
        "सवा दो सौ",
        "साढ़े तीन सौ",
        "साढ़े चार लाख",
        "पौने चार",
        "पौने दो सौ",
        "पौने चार सौ",
        "डेढ़ करोड़",
        "ढाई हज़ार",
        "डेढ़",
        "दो करोड़ पचास लाख",
        "पाँच सौ पाँच",
        "निन्यानवे",
    ],
)
def test_hindi_phrases_match_oracle(phrase):
    assert Fraction(single_value(phrase)) == eval_hindi(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "twenty-five",
        "five hundred and five",
        "one hundred and five",
        "half a million",
        "a quarter of a crore",
        "two crore fifty lakh",
        "a hundred",
    ],
)
def test_english_phrases_match_oracle(phrase):
    assert Fraction(single_value(phrase)) == eval_english(phrase)


# -- edge-case table -------------------------------------------------------------------------
# (text, [(value, type, currency)])
TABLE = [
    ("Pay ₹500 and $20 now.", [("500", "MONEY", "INR"), ("20", "MONEY", "USD")]),
    ("रुपये 500 मात्र", [("500", "MONEY", "INR")]),
    ("₹500 ₹600", [("500", "MONEY", "INR"), ("600", "MONEY", "INR")]),
    ("500 INR", [("500", "MONEY", "INR")]),
    ("INR 5,000", [("5000", "MONEY", "INR")]),
    ("USD500", [("500", "MONEY", "USD")]),
    ("Rs.500", [("500", "MONEY", "INR")]),
    ("रु. 500", [("500", "MONEY", "INR")]),
    ("a hundred dollars", [("100", "MONEY", "USD")]),
    ("5 से 10 रुपये", [("5", "COUNT", None), ("10", "MONEY", "INR")]),
    ("5 pounds of rice", [("5", "COUNT", None)]),
    ("a dozen eggs", [("12", "COUNT", None)]),
    ("two dozen", [("24", "COUNT", None)]),
    ("half a dozen", [("6", "COUNT", None)]),
    ("2 and a half lakh", [("250000", "COUNT", None)]),
    ("one and a half million", [("1500000", "COUNT", None)]),
    ("1½ lakh", [("150000", "COUNT", None)]),
    ("two thousand five hundred", [("2500", "COUNT", None)]),
    ("one million two hundred thousand", [("1200000", "COUNT", None)]),
    ("between five and ten", [("5", "COUNT", None), ("10", "COUNT", None)]),
    ("between one hundred and two hundred", [("100", "COUNT", None), ("200", "COUNT", None)]),
    ("five 10", [("5", "COUNT", None), ("10", "COUNT", None)]),
    ("5 lakh 20", [("500000", "COUNT", None), ("20", "COUNT", None)]),
    ("5 lakh 20 thousand", [("520000", "COUNT", None)]),
    ("पाँच हज़ार दो सौ", [("5200", "COUNT", None)]),
    ("डेढ़ सौ रुपये", [("150", "MONEY", "INR")]),
    ("आधा लाख", [("50000", "COUNT", None)]),
    (
        "50% off, 12.5 per cent, ५ प्रतिशत",
        [("50", "PERCENT", None), ("12.5", "PERCENT", None), ("5", "PERCENT", None)],
    ),
    (
        "from -5 to -10 and 5-10",
        [
            ("-5", "COUNT", None),
            ("-10", "COUNT", None),
            ("5", "COUNT", None),
            ("10", "COUNT", None),
        ],
    ),
    ("Take .5 kg", [("0.5", "COUNT", None)]),
    ("दो-तीन लोग", [("2", "COUNT", None), ("3", "COUNT", None)]),
    ("5km and 10kg", [("5", "COUNT", None), ("10", "COUNT", None)]),
    ("कीमत ₹५००-₹६०० है", [("500", "MONEY", "INR"), ("600", "MONEY", "INR")]),
    (
        "कीमत 500 रुपये से गिरकर 400 रुपये हो गई, इसलिए शुक्रवार के बाद भुगतान न करें।",
        [("500", "MONEY", "INR"), ("400", "MONEY", "INR")],
    ),
    (
        "The price fell from Rs. 500 to Rs. 400, so do not pay after Friday.",
        [("500", "MONEY", "INR"), ("400", "MONEY", "INR")],
    ),
]


@pytest.mark.parametrize("text,expected", TABLE, ids=[t[0][:40] for t in TABLE])
def test_edge_case_table(text, expected):
    _, numeric = analyze(text)
    got = [(str(q.value), q.type, q.currency) for q in numeric.quantities]
    assert got == expected
    assert not numeric.errors


@pytest.mark.parametrize(
    "text",
    [
        "I have 3 crore 5 crore",
        "5 lakh 2 crore",
        "दो लाख तीन करोड़",
        "two thousand three million",
    ],
)
def test_scale_order_errors_yield_no_quantity(text):
    _, numeric = analyze(text)
    assert [e.code for e in numeric.errors] == ["SCALE_ORDER"]
    assert numeric.quantities == []


@pytest.mark.parametrize(
    "text,hour,minute,period",
    [
        ("साढ़े पाँच बजे", 5, 30, None),
        ("सवा चार बजे", 4, 15, None),
        ("पौने छह बजे", 5, 45, None),
        ("डेढ़ बजे", 1, 30, None),
        ("ढाई बजे", 2, 30, None),
        ("5 pm", 5, 0, "PM"),
        ("10AM", 10, 0, "AM"),
        ("10:30 AM", 10, 30, "AM"),
        ("18:30", 18, 30, None),
        ("५ बजे", 5, 0, None),
        ("पाँच बजे", 5, 0, None),
        ("at 5 o'clock", 5, 0, None),
        ("पौने एक बजे", 12, 45, None),
    ],
)
def test_times(text, hour, minute, period):
    _, numeric = analyze(text)
    assert numeric.quantities == []
    assert [(t.hour, t.minute, t.period) for t in numeric.times] == [(hour, minute, period)]


@pytest.mark.parametrize("text", ["13 PM", "25 बजे", "0 AM"])
def test_invalid_times_stay_quantities(text):
    _, numeric = analyze(text)
    assert numeric.times == [] and len(numeric.quantities) == 1


@pytest.mark.parametrize(
    "text,flag",
    [
        ("मेरे पास एक कार है।", "WEAK_ONE"),
        ("I have one ticket.", "WEAK_ONE"),
        ("मुझे पैसे दो।", "AMBIGUOUS_NUMWORD"),
        ("half price", "FRACTION_WORD"),
        ("5 pounds", "AMBIGUOUS_CURRENCY"),
        ("lakh rupees", "BARE_SCALE"),
    ],
)
def test_quantity_flags(text, flag):
    _, numeric = analyze(text)
    assert any(flag in q.flags for q in numeric.quantities), text


@pytest.mark.parametrize("text", ["One of them left.", "The one that I saw.", "which one?"])
def test_one_as_pronoun_is_not_a_quantity(text):
    _, numeric = analyze(text)
    assert numeric.quantities == []


def test_money_with_one_is_not_weak():
    _, numeric = analyze("एक रुपया")
    assert numeric.quantities[0].type == "MONEY"
    assert "WEAK_ONE" not in numeric.quantities[0].flags


def test_values_are_decimal_and_clean():
    _, numeric = analyze("आधा लाख, सवा दो सौ, 1.500, half a dozen")
    values = [q.value for q in numeric.quantities]
    assert all(isinstance(v, Decimal) for v in values)
    assert [str(v) for v in values] == ["50000", "225", "1.5", "6"]


def test_conflicting_currency_words_are_flagged():
    _, numeric = analyze("Pay 500 Rs 600 USD.")
    assert "CURRENCY_CONFLICT" in numeric.quantities[1].flags


def test_bare_rs_gets_non_quantity_flag():
    lexed, numeric = analyze("He wrote Rs. in the margin.")
    assert "NON_QUANTITY" in numeric.flags and numeric.quantities == []


@pytest.mark.parametrize(
    "text", ["Sales crossed ₹3.5 crore this quarter.", "Results for the next quarter."]
)
def test_bare_quarter_is_a_period_not_a_fraction(text):
    _, numeric = analyze(text)
    assert all(q.value != Decimal("0.25") for q in numeric.quantities)


def test_a_quarter_still_counts():
    _, numeric = analyze("Add a quarter of a crore and three quarters.")
    assert [str(q.value) for q in numeric.quantities] == ["2500000", "0.75"]
