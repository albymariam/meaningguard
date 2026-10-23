"""A plain-words suggestion for fixing a finding. Text only: nothing here edits a translation.

Numbers and currency are repaired automatically (repair.py). For every other error the verifier
says what is wrong and this module says what to change, with the Hindi word to use.
"""

from __future__ import annotations

from .verifier import Finding, describe_diff

TIMEREL = {
    "ON_OR_BEFORE": "तक (by / until)",
    "BEFORE": "से पहले (before)",
    "AFTER": "के बाद (after)",
    "ON_OR_AFTER": "से / के बाद से (from / since)",
    "ON": "को / पर (on)",
}
DIRECTION = {"RISE": "बढ़ना (rose / up)", "FALL": "घटना (fell / down)"}
COMPARISON = {
    "GTE": "कम से कम (at least)",
    "LTE": "अधिकतम / से ज़्यादा नहीं (at most)",
    "GT": "से अधिक (more than)",
    "LT": "से कम (less than)",
}
CHANGE = {
    "NUMBER_CHANGED",
    "CURRENCY_CHANGED",
    "CURRENCY_MISSING",
    "CURRENCY_ADDED",
    "DATE_CHANGED",
    "TIME_CHANGED",
    "TIME_PERIOD_MISSING",
    "DATE_PRECISION_CHANGED",
    "LITERAL_CHANGED",
    "VAGUE_QUANTITY_CHANGED",
}


def _tail(message: str) -> str:
    return message.split(": ", 1)[1].rstrip(".") if ": " in message else message


def hint(f: Finding) -> str | None:
    """What to change in the Hindi, or None when there is no concrete suggestion."""
    code = f.code
    diff = f.diffs[0] if f.diffs else None
    if code == "NEGATION_DROPPED":
        return "Add the negation (न / नहीं / मत) so the Hindi also says 'not'."
    if code == "NEGATION_ADDED":
        return "Remove the extra negation (न / नहीं): the English is not negated."
    if code == "NEGATION_UNCERTAIN":
        return "Read this clause yourself: the negation could not be matched with confidence."
    if code == "DIRECTION_CHANGED" and diff:
        word = DIRECTION.get(str(diff.src))
        return f"Use a verb of {word} instead." if word else None
    if code == "TIMEREL_CHANGED" and diff:
        word = TIMEREL.get(str(diff.src))
        return f"Use {word} with the date or time." if word else None
    if code == "COMPARISON_CHANGED" and diff:
        word = COMPARISON.get(str(diff.src))
        return f"Use {word} instead." if word else None
    if code in CHANGE and diff:
        src, tgt = describe_diff(diff)
        return f"Change {tgt} to {src}."
    if code == "FACT_MISSING":
        return f"Add this to the Hindi: {_tail(f.message)}."
    if code == "FACT_ADDED":
        return f"Check or remove what the Hindi adds: {_tail(f.message)}."
    return None
