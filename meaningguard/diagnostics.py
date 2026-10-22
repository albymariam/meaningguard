"""Diagnostic codes: severity and the default message (spec 05 section 7, 01 section 5, 06)."""

from __future__ import annotations

from .nodes import Diagnostic

# code -> (severity, message)
CODES = {
    # lexer flags (01 section 5)
    "MIXED_DIGIT_SCRIPT": ("warning", "A number mixes digit scripts."),
    "COMMA_DECIMAL_SUSPECT": ("warning", "A comma may be a decimal separator here."),
    "DOT_THOUSANDS_SUSPECT": ("warning", "A dot may be a thousands separator here."),
    "AMBIGUOUS_ABBREV": (
        "info",
        "A one-letter scale (l, m, b) can also mean litres, metres or bytes.",
    ),
    "NON_QUANTITY": (
        "info",
        "A phone number, version, ordinal or identifier was kept as an exact string.",
    ),
    "NEGATIVE": ("info", "A negative number."),
    "INFORMAL_NEG": ("info", "An informal negation without an apostrophe."),
    "AMBIGUOUS_CURRENCY": ("warning", "The currency word has more than one meaning."),
    "CURRENCY_CONFLICT": ("warning", "Two different currencies are next to one number."),
    "WEAK_ONE": ("info", "'one' or 'एक' may be an article, not a quantity."),
    "AMBIGUOUS_NUMWORD": (
        "info",
        "The number word may be another word (for example the verb 'दो').",
    ),
    "FRACTION_WORD": ("info", "A fraction word with no scale."),
    "BARE_SCALE": ("info", "A scale word with no number."),
    "MIXED_SCRIPT": ("info", "The sentence mixes Latin and Devanagari words."),
    "UNTRANSLATED_LATIN": (
        "warning",
        "The Hindi text contains Latin words that were not translated.",
    ),
    "SCALE_ORDER": ("error", "The scales of a number are in the wrong order."),
    # parser
    "UNSUPPORTED_STRUCTURE": (
        "warning",
        "An 'or' alternative is not modelled; its facts are listed as separate facts.",
    ),
    "UNATTACHED_REL": ("info", "A relation word has no recognised operand."),
    "SKIPPED_IN_ISLAND": ("info", "Words were skipped inside a recognised phrase."),
    "AMBIGUOUS_FROM": ("warning", "'से' without a partner cannot be read as 'from' or 'than'."),
    "AMBIGUOUS_PARTICIPLE": (
        "info",
        "'से बढ़कर' is read as a change because a value follows; 'greater than' is also possible.",
    ),
    "UNASSEMBLED_NUMBER": ("warning", "A number could not be assembled into a quantity."),
    "COND_INCOMPLETE": ("warning", "A conditional word has no matching clause."),
    "MULTI_SENTENCE": (
        "info",
        "The input has more than one sentence; conditionals do not cross sentences.",
    ),
    "INTERNAL_PARSE_ERROR": (
        "error",
        "The parser failed on a clause; only lexer-level facts are kept.",
    ),
    "TAK_AS_UPTO": ("info", "'तक' after a number was read as 'up to'."),
    "DATE_ORDER_ASSUMED": ("info", "A numeric date was read day first."),
    "PERIOD_FROM_DAYPART": ("info", "AM or PM was taken from a part of the day."),
    "IMPLICIT_ON": ("info", "A time or date with no relation word was read as 'on'."),
    "WEAK_ONE_DROPPED": ("info", "A lone 'one' or 'एक' may be an article and was not kept."),
    "APPROX_IN_COMPOUND": ("info", "An approximation word inside a comparison is not kept."),
    # semantics (02, 06)
    "SOURCE_INCONSISTENT": (
        "warning",
        "The change verb contradicts the numbers; the direction was computed from the numbers.",
    ),
    "SCALE_SHARED": ("info", "A bare number took the scale of the number after it ('5-10 lakh')."),
    "PERIOD_SHARED": ("info", "A bare hour took the AM or PM of the time after it ('5 to 7 PM')."),
    "CHANGE_NO_DELTA": ("info", "The change has equal start and end values."),
    "RANGE_REVERSED": ("warning", "A range lists the larger value first."),
    "CURRENCY_INHERITED": (
        "info",
        "A number took the currency of its partner in the same construct.",
    ),
    "PERCENT_INHERITED": (
        "info",
        "A number took the percent sign of its partner in the same construct.",
    ),
    "UNIT_INHERITED": (
        "info",
        "A number took the duration unit of its partner in the same construct.",
    ),
    "CURRENCY_MISMATCH_IN_RANGE": ("warning", "The two ends of a range use different currencies."),
    "TIME_PERIOD_MISSING": ("info", "A 12-hour time has no AM or PM."),
    "BY_UNTIL_AMBIGUOUS": ("info", "'by' and 'until' are both read as 'on or before'."),
    "DOUBLE_NEG": ("warning", "Two negations in one clause cancel out."),
    "UNTIL_AS_UNLESS": (
        "info",
        "'जब तक ... नहीं' is read as 'unless'; the approximation is recorded.",
    ),
    "UNTIL_AS_IF": ("info", "'जब तक' without a negation is read as 'if'."),
    "UNLESS_REWRITTEN": ("info", "'unless' was rewritten as 'if not'."),
    "NEG_REWRITTEN": ("info", "A negated relation was rewritten as the opposite relation."),
}


def make(code: str, span=None, message: str | None = None) -> Diagnostic:
    severity, default = CODES.get(code, ("info", code))
    return Diagnostic(code, severity, tuple(span) if span else None, message or default)
