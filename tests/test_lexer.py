"""Lexer unit tests: normalization, offsets, sentences, words, dates, negation roles."""

import pytest

from meaningguard.lexer import (
    EmptyInput,
    InputTooLong,
    lex,
    normalize,
)


def kinds(text, lang=None):
    return [(t.type, t.text) for t in lex(text, lang).tokens]


# -- normalization ---------------------------------------------------------------------------
def test_normalize_maps_offsets_back_to_the_original():
    original = "पाँच हज‌ार  रुपये"
    norm = normalize(original)
    assert norm.text == "पाँच हजार रुपये"
    start = norm.text.index("हजार")
    a, b = norm.span(start, start + 4)
    assert original[a:b] == "हज‌ार"


def test_normalize_nfc_nukta_and_nbsp():
    norm = normalize("Pay\xa0\xa0₹500’s – now−")
    assert norm.text == "Pay ₹500's - now-"


def test_token_offsets_point_into_the_original_text():
    original = "  Pay\xa0₹500   today. "
    result = lex(original)
    for tok in result.tokens:
        a, b = tok.orig
        assert normalize(original[a:b]).text == tok.text
    number = next(t for t in result.tokens if t.type == "NUMBER")
    assert original[number.orig[0] : number.orig[1]] == "500"


@pytest.mark.parametrize("text,exc", [("", EmptyInput), ("   \t\n", EmptyInput),
                                      ("​‌", EmptyInput), ("a" * 1001, InputTooLong)])
def test_input_contract(text, exc):
    with pytest.raises(exc):
        lex(text)


def test_exactly_1000_characters_is_accepted():
    assert lex("a" * 1000).tokens


# -- sentences -------------------------------------------------------------------------------
@pytest.mark.parametrize("text,count", [
    ("Pay now.", 1), ("Pay now. Then leave.", 2), ("Rs. 500 is due. Pay now.", 2),
    ("Mr. Rao paid. He left.", 2), ("See No. 5. It is open.", 2), ("I said no. Then left.", 2),
    ("It costs 2.5 lakh. Pay.", 2), ("Wait... what?", 1), ("What?! Really?", 2),
    ("पैसे दो। जल्दी करो॥", 2), ("He said \"pay now.\" Then left.", 2),
    ("About 5 pm. 5 people came.", 2), ("approx. 5 people came.", 1),
    ("Pay A. Rao.", 1), ("pay now", 1),
])
def test_sentence_count(text, count):
    assert lex(text).sentence_count == count


def test_danda_never_hides_a_negation():
    result = lex("भुगतान नहीं।")
    assert [t.type for t in result.tokens] == ["WORD", "NEG", "SENT_END"]


# -- words, punctuation, hyphens -------------------------------------------------------------
def test_hyphenated_words_stay_whole_unless_number_words():
    assert ("WORD", "state-of-the-art") in kinds("a state-of-the-art fee")
    assert ("WORD", "non-refundable") in kinds("a non-refundable fee")
    assert [t.type for t in lex("twenty-five").tokens] == ["NUMWORD", "PUNCT", "NUMWORD"]


def test_apostrophe_words():
    assert ("NEG", "don't") in kinds("I don't know")
    assert ("WORD", "John's") in kinds("John's fee")
    assert ("NEG", "dont") in kinds("I dont know")
    assert "INFORMAL_NEG" in lex("I dont know").flags


def test_longest_match_wins():
    assert ("REL", "कम से कम") in kinds("कम से कम ₹५०० दें।")
    assert ("REL", "से ज़्यादा") in kinds("₹५०० से ज़्यादा")
    assert ("NEG", "कभी नहीं") in kinds("मैं कभी नहीं जाऊँगा।")
    assert ("NEG", "no longer") in kinds("It is no longer valid.")
    assert ("NEG", "कुछ नहीं") in kinds("कुछ नहीं मिला।")


def test_nukta_and_chandrabindu_variants_share_a_key():
    for spelling in ("हज़ार", "हजार", "हज़ार"):
        assert ("SCALE", spelling) in kinds(f"पाँच {spelling} रुपये")
    assert ("NUMWORD", "पांच") in kinds("पांच रुपये")


def test_alt_split_for_participle_vs_comparison():
    tokens = lex("₹४०० से बढ़कर ₹५०० हो गया").tokens
    tok = next(t for t in tokens if t.type == "REL" and "बढ़" in t.text)
    assert tok.attrs["role"] == "GT"
    assert tok.attrs["alt_split"] == (("FROM_OR_THAN", "से"), ("RISE_PART", "बढ़कर"))


# -- scripts and flags -----------------------------------------------------------------------
def test_mixed_script_and_untranslated_latin():
    result = lex("कीमत discount के बाद ₹५०० है", "hi")
    assert "MIXED_SCRIPT" in result.flags and "UNTRANSLATED_LATIN" in result.flags
    assert "UNTRANSLATED_LATIN" not in lex("कीमत ₹५०० है", "hi").flags
    assert "UNTRANSLATED_LATIN" not in lex("The discount is 5%", "en").flags


def test_latin_script_hindi_is_not_supported():
    assert ("WORD", "nahi") in kinds("bhugtan nahi karo")
    assert lex("bhugtan nahi karo").neg_count == 0


# -- numbers that are not quantities ---------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Call 9876543210 today.", "Call +91 98765 43210 now.", "Version 1.2.3 is out.",
    "Due on 15/10/2026.", "Due 2026-10-22.", "Room A5 is free.", "Use H2O.", "COVID-19 cases.",
    "The 1st and 22nd prize.", "५वीं मंज़िल पर।", "See item No. 5 now.",
])
def test_non_quantities_have_no_number_token(text):
    result = lex(text)
    assert not result.of_type("NUMBER"), result.tokens
    assert "NON_QUANTITY" in result.flags


def test_opaque_tokens_keep_their_ascii_digits():
    token = next(t for t in lex("Call ९८७६५४३२१०।").tokens if t.attrs.get("opaque") == "PHONE")
    assert token.attrs["digits"] == "9876543210"


@pytest.mark.parametrize("text,flag", [
    ("12,34", "COMMA_DECIMAL_SUSPECT"), ("1.500", "DOT_THOUSANDS_SUSPECT"),
    ("१२3", "MIXED_DIGIT_SCRIPT"), ("5L", "AMBIGUOUS_ABBREV"), ("5m", "AMBIGUOUS_ABBREV"),
])
def test_number_flags(text, flag):
    assert flag in lex(text).flags


@pytest.mark.parametrize("text", ["1,234", "12,34,567", "1,234,567.89", "5k", "3cr", "0.5"])
def test_clean_numbers_have_no_flags(text):
    assert lex(text).flags == set()


def test_ten_digit_threshold_and_grouped_big_numbers():
    assert not lex("1000000000").of_type("NUMBER")  # 10 digits, no separators: phone-like
    assert lex("1,00,00,00,000").of_type("NUMBER")  # grouped: a real number


def test_time_tokens():
    token = lex("at 10:30").tokens[-1]
    assert token.type == "TIME" and (token.attrs["hour"], token.attrs["minute"]) == (10, 30)
    assert lex("ratio 10:75").of_type("TIME") == []


def test_bare_scale_words_that_need_a_number_become_plain_words():
    assert lex("Arab countries").tokens[0].type == "WORD"
    assert lex("अरब देश").tokens[0].type == "WORD"
    assert lex("दो अरब").tokens[1].type == "SCALE"
    assert lex("5 cr").tokens[1].type == "SCALE"
    assert lex("cr").tokens[0].type == "WORD"


# -- calendar words --------------------------------------------------------------------------
@pytest.mark.parametrize("text,date_word", [
    ("He may come in May.", "May"), ("Meet on May 5", "May"), ("Pay by may", "may"),
    ("Meet on Sat", "Sat"), ("Pay Mar 5", "Mar"), ("in march", "march"),
])
def test_ambiguous_date_words_that_are_dates(text, date_word):
    assert any(t.type in ("DAY", "MONTH") and t.text == date_word for t in lex(text).tokens)


@pytest.mark.parametrize("text", [
    "He may come.", "I sat down.", "May I come?", "The sun is hot.", "They march on.",
    "Sat on the chair.",
])
def test_ambiguous_date_words_that_are_not_dates(text):
    assert not lex(text).of_type("DAY", "MONTH")


def test_hindi_calendar_words():
    assert lex("शुक्रवार के बाद").tokens[0].attrs["id"] == 4
    assert lex("५ जनवरी").tokens[1].attrs["id"] == 1


# -- negation roles --------------------------------------------------------------------------
@pytest.mark.parametrize("text,role", [
    ("Not only the price rose.", "NOT_ONLY"), ("No matter what.", "CONCESSIVE"),
    ("No doubt it rose.", "EMPHASIS"), ("यह ठीक है न?", "TAG_QUESTION"),
    ("वे ठीक हैं न", "TAG_QUESTION"), ("क्यों न जाएँ?", "SUGGESTION"),
    ("Why not go?", "SUGGESTION"), ("कहीं न कहीं यह सही है।", "EMPHASIS"),
    ("चलो ना, चलते हैं।", "AMBIG"),
])
def test_non_counting_negators_have_a_role(text, role):
    result = lex(text)
    assert result.neg_count == 0
    assert role in {t.attrs.get("role") for t in result.of_type("NEG")}


def test_tag_question_needs_sentence_end():
    assert lex("यह ठीक है न करो।").neg_count == 1


@pytest.mark.parametrize("text,count", [
    ("He did not come, nor did she.", 1), ("Neither he nor she came.", 1),
    ("वह न आया न गया।", 1), ("I can't not go.", 2), ("No one has nothing.", 2),
    ("It is not bad, not at all.", 2), ("Don't never do that.", 2),
])
def test_negation_counting(text, count):
    assert lex(text).neg_count == count


def test_negation_counts_reset_per_sentence():
    assert lex("Neither came. Neither left.").neg_count == 2
