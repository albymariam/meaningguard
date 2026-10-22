"""Repair (spec 06 section 5): fix a wrong number or currency in the translation, minimally.

Only the target text is edited, only for NUMBER_CHANGED, CURRENCY_CHANGED and CURRENCY_MISSING,
and only when the pair is unambiguous. The new number is written in the target's own notation
(digit script, scale word, grouping). After the edits the text is compiled and verified again; a
group of edits that does not remove its finding cleanly is discarded.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from . import numerics as num
from .ir import IR, compile_text
from .lexer import CURRENCY, NUMBER, NUMWORD, SCALE
from .nodes import dec_str
from .parser import item_value
from .verifier import SYMBOL, Finding, Report, verify

REPAIRABLE = {"NUMBER_CHANGED", "CURRENCY_CHANGED", "CURRENCY_MISSING"}
NUMERIC_TOKENS = (NUMBER, NUMWORD, SCALE)
HINDI_SCALES = (
    (Decimal(10) ** 9, "अरब"),
    (Decimal(10) ** 7, "करोड़"),
    (Decimal(10) ** 5, "लाख"),
    (Decimal(10) ** 3, "हज़ार"),
)
CURRENCY_WORD = {"INR": "रुपये", "USD": "डॉलर", "EUR": "यूरो", "GBP": "पाउंड", "JPY": "येन"}
DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")
DEVANAGARI_RANGE = "०१२३४५६७८९"


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    old: str
    new: str
    code: str  # the finding it fixes

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "old": self.old,
            "new": self.new,
            "code": self.code,
        }


@dataclass
class RepairResult:
    text: str  # the target text after repair (normalized text of the input when unchanged)
    edits: list
    reverted: list  # findings whose edits were discarded: {"code", "message"}
    before: Report
    after: Report
    target: IR

    @property
    def repaired(self) -> bool:
        return bool(self.edits)

    def to_dict(self) -> dict:
        return {
            "repaired": self.repaired,
            "text": self.text,
            "edits": [e.to_dict() for e in self.edits],
            "reverted": self.reverted,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
        }


# ---------------------------------------------------------------------------------------------
# Writing a number in the target's own notation


def _group(digits: str, style: str) -> str:
    if style == "none" or len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    step = 2 if style == "indian" else 3
    parts = []
    while head:
        parts.insert(0, head[-step:])
        head = head[:-step]
    return ",".join(parts + [tail])


def _grouping_of(number_tokens: list, currency: str | None) -> str:
    """'none', 'indian' or 'western': copied from the target's own number when it has commas."""
    for tok in number_tokens:
        if tok.type == NUMBER and "," in tok.text:
            middle = tok.text.split(",")[1:-1]
            if any(len(part) == 2 for part in middle):
                return "indian"
            if any(len(part) == 3 for part in middle):
                return "western"
            return "western" if currency not in (None, "INR") else "indian"
    return "none"


def _mantissa_ok(amount: Decimal, scale: Decimal) -> bool:
    m = amount / scale
    return m >= 1 and m == m.quantize(Decimal("0.01"))


def render_number(amount: Decimal, tokens: list, currency: str | None, dev_text: bool):
    """Text for ``amount`` in the notation of the target number ``tokens``, or None."""
    number_tokens = [t for t in tokens if t.type in NUMERIC_TOKENS]
    scales = [t for t in number_tokens if t.type == SCALE]
    if len(scales) > 1 or amount < 0:
        return None
    digit_tokens = [t for t in number_tokens if t.type == NUMBER]
    if digit_tokens:
        dev = any(c in DEVANAGARI_RANGE for c in digit_tokens[0].text)
    else:
        dev = dev_text
    scale_word = None
    if scales:
        own = scales[0]
        choices = [(own.value, own.text)] + [s for s in HINDI_SCALES if s[0] != own.value]
        for value, word in choices:
            if _mantissa_ok(amount, value):
                amount, scale_word = amount / value, word
                break
    if scale_word:
        body = dec_str(amount)
    else:
        whole, _, frac = dec_str(amount).partition(".")
        body = _group(whole, _grouping_of(number_tokens, currency)) + ("." + frac if frac else "")
    if dev:
        body = body.translate(DEVANAGARI_DIGITS)
    return f"{body} {scale_word}" if scale_word else body


def render_currency(code: str, style: str, amount: Decimal | None):
    if style == "symbol":
        return SYMBOL.get(code)
    if style == "code":
        return code
    if code == "INR" and amount == 1:
        return "रुपया"
    return CURRENCY_WORD.get(code)


# ---------------------------------------------------------------------------------------------
# Planning


def _same_kind(sv: dict, tv: dict) -> bool:
    kinds = {sv["type"], tv["type"]}
    if kinds <= {"MONEY", "COUNT"}:
        return True
    return sv["type"] == tv["type"] and (sv["type"] != "DURATION" or sv["unit"] == tv["unit"])


def _plan_finding(finding: Finding, target: IR, res, items: list):
    """Edits for one finding, or None when any precondition fails."""
    span = finding.target["span"]
    edits: list = []
    by_path: dict = {}
    for d in finding.diffs:
        by_path.setdefault(d.path, []).append(d)
    for diffs in by_path.values():
        sv, tv = diffs[0].src, diffs[0].tgt
        if not isinstance(sv, dict) or not isinstance(tv, dict) or not _same_kind(sv, tv):
            return None
        pool = []
        for item in items:
            first, last = res.tokens[item.start], res.tokens[item.end - 1]
            if not (span[0] <= first.start and last.end <= span[1]):
                continue
            value = item_value(item).to_dict()
            if value["amount"] == tv["amount"] and (
                tv["type"] == "DURATION" and value["type"] == "COUNT" or value["type"] == tv["type"]
            ):
                pool.append(item)
        if len(pool) != 1:
            return None
        item = pool[0]
        tokens = res.tokens[item.start : item.end]
        numbers = [t for t in tokens if t.type in NUMERIC_TOKENS]
        currencies = [t for t in tokens if t.type == CURRENCY]
        if not numbers or len(currencies) > 1:
            return None
        codes = {d.code for d in diffs}
        new_amount = Decimal(sv["amount"])
        code = sv.get("currency") if sv["type"] == "MONEY" else None
        if "NUMBER_CHANGED" in codes:
            text = render_number(
                new_amount,
                tokens,
                code or (tv.get("currency")),
                any(c in DEVANAGARI_RANGE for c in target.text),
            )
            if text is None:
                return None
            a, b = numbers[0].start, numbers[-1].end
            edits.append(Edit(a, b, target.text[a:b], text, finding.code))
        if "CURRENCY_CHANGED" in codes:
            if not currencies:
                return None
            tok = currencies[0]
            new = render_currency(code, tok.attrs["style"], new_amount)
            if new is None:
                return None
            edits.append(Edit(tok.start, tok.end, tok.text, new, finding.code))
        if "CURRENCY_MISSING" in codes:
            if currencies:
                return None
            symbols = any(c in target.text for c in SYMBOL.values())
            new = render_currency(code, "symbol" if symbols else "word", new_amount)
            if new is None:
                return None
            if symbols:
                edits.append(Edit(numbers[0].start, numbers[0].start, "", new, finding.code))
            else:
                edits.append(Edit(numbers[-1].end, numbers[-1].end, "", " " + new, finding.code))
    return edits or None


def _eligible(finding: Finding, report: Report) -> bool:
    return (
        finding.code in REPAIRABLE
        and finding.unique
        and finding.source is not None
        and finding.target is not None
        and report.source_tier == 1
        and report.target_tier in (1, 2)
        and all(d.code in REPAIRABLE for d in finding.diffs)
    )


# ---------------------------------------------------------------------------------------------
# Applying and checking


def _apply(text: str, edits: list) -> str:
    for e in sorted(edits, key=lambda e: (e.start, e.end), reverse=True):
        text = text[: e.start] + e.new + text[e.end :]
    return text


def _key(f: Finding) -> tuple:
    return (f.code, f.source["fp"] if f.source else None, f.clause)


def _safe(old: Report, new: Report, finding: Finding) -> bool:
    """Spec 06 section 5: the finding is gone, nothing new is wrong, nothing matched is lost."""
    fp = finding.source["fp"]
    if any(f.source and f.source["fp"] == fp for f in new.findings):
        return False
    old_bad = Counter(_key(f) for f in old.findings if f.severity != "info")
    new_bad = Counter(_key(f) for f in new.findings if f.severity != "info")
    if new_bad - old_bad:
        return False
    return not (Counter(old.matched) - Counter(new.matched))


def repair(source: IR, target_text: str, apply: bool = True) -> RepairResult:
    """One repair pass over the translation of ``source`` (``apply=False`` only verifies)."""
    target = compile_text(target_text, "hi")
    before = verify(source, target)
    res, nres = num.analyze(target.text, "hi")
    items = [i for i in nres.items if i.kind == "QUANTITY"]
    plans = []
    for finding in before.findings if apply else []:
        if _eligible(finding, before):
            edits = _plan_finding(finding, target, res, items)
            if edits:
                plans.append((finding, edits))
    text, current, current_ir = target.text, before, target
    applied: list = []
    reverted: list = []
    # Later parts of the text first, so the offsets of the earlier plans stay valid.
    for finding, edits in sorted(plans, key=lambda p: -min(e.start for e in p[1])):
        candidate = _apply(text, edits)
        ir = compile_text(candidate, "hi")
        report = verify(source, ir)
        if _safe(current, report, finding):
            text, current, current_ir = candidate, report, ir
            applied.extend(edits)
        else:
            reverted.append({"code": "REPAIR_REVERTED", "message": finding.message})
    applied.sort(key=lambda e: e.start)
    return RepairResult(text, applied, reverted, before, current, current_ir)
