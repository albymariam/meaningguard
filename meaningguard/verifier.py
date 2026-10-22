"""Verifier (spec 06): compare the IR of the source with the IR of its translation.

Pure and deterministic. Clauses are aligned, facts are matched by fingerprint, the rest are paired
by kind and compared field by field. Each pair gives one finding whose code is the most specific
difference; every difference is kept in ``Finding.diffs`` so repair can act on a single field.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ir import IR

ERROR, WARNING, INFO = "error", "warning", "info"

SEVERITY = {
    "FACT_MISSING": ERROR,
    "FACT_ADDED": WARNING,
    "NUMBER_CHANGED": ERROR,
    "CURRENCY_CHANGED": ERROR,
    "CURRENCY_MISSING": ERROR,
    "CURRENCY_ADDED": WARNING,
    "DIRECTION_CHANGED": ERROR,
    "COMPARISON_CHANGED": ERROR,
    "TIMEREL_CHANGED": ERROR,
    "DATE_CHANGED": ERROR,
    "TIME_CHANGED": ERROR,
    "TIME_PERIOD_MISSING": WARNING,
    "DATE_PRECISION_CHANGED": WARNING,
    "PRECISION_CHANGED": WARNING,
    "VAGUE_QUANTITY_CHANGED": ERROR,
    "LITERAL_CHANGED": ERROR,
    "NEGATION_DROPPED": ERROR,
    "NEGATION_ADDED": ERROR,
    "NEGATION_UNCERTAIN": WARNING,
    "CONDITION_CHANGED": WARNING,
    "CLAUSE_COUNT_CHANGED": WARNING,
    "SOURCE_INCONSISTENT": WARNING,
    "AMBIGUOUS_NUMBER": WARNING,
    "UNVERIFIED": INFO,
    "TONE_POLARITY_FLIPPED": WARNING,
    "SARCASM_POSSIBLY_LOST": WARNING,
    "TONE_INTENSIFIED": INFO,
    "TONE_WEAKENED": INFO,
    "EMOTION_CHANGED": INFO,
    "TONE_UNCERTAIN": INFO,
}
TONE_CODES = (
    "TONE_POLARITY_FLIPPED",
    "SARCASM_POSSIBLY_LOST",
    "TONE_INTENSIFIED",
    "TONE_WEAKENED",
    "EMOTION_CHANGED",
    "TONE_UNCERTAIN",
)
TONE_STRONG = 0.7  # confidence needed before a change between neutral and polar is reported

# Most specific code first (spec 06 section 2); the first one present in a pair names the finding.
PRECEDENCE = (
    "DIRECTION_CHANGED",
    "CURRENCY_CHANGED",
    "CURRENCY_MISSING",
    "CURRENCY_ADDED",
    "COMPARISON_CHANGED",
    "TIMEREL_CHANGED",
    "VAGUE_QUANTITY_CHANGED",
    "LITERAL_CHANGED",
    "DATE_CHANGED",
    "TIME_CHANGED",
    "NUMBER_CHANGED",
    "TIME_PERIOD_MISSING",
    "DATE_PRECISION_CHANGED",
    "PRECISION_CHANGED",
    "FACT_MISSING",
    "FACT_ADDED",
    "UNVERIFIED",
)
NUMBERISH = {
    "NUMBER_CHANGED",
    "CURRENCY_CHANGED",
    "CURRENCY_MISSING",
    "CURRENCY_ADDED",
    "VAGUE_QUANTITY_CHANGED",
}
NUMBER_FLAGS = {"COMMA_DECIMAL_SUSPECT", "DOT_THOUSANDS_SUSPECT", "MIXED_DIGIT_SCRIPT"}
NUMERIC_TYPES = ("MONEY", "COUNT", "PERCENT", "DURATION")
VALUE_KEYS = {
    "QUANT": ("value",),
    "CHANGE": ("from", "to", "delta"),
    "RANGE": ("low", "high"),
    "BOUND": ("value",),
    "TIMEREL": ("anchor",),
}
SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


@dataclass(frozen=True)
class Diff:
    """One difference between a source fact and a target fact."""

    path: str  # 'value', 'from', 'to', 'delta', 'low', 'high', 'anchor', 'op', 'rel', ...
    code: str
    src: object = None
    tgt: object = None


@dataclass
class Finding:
    code: str
    severity: str
    message: str
    clause: int | None = None  # source clause index
    source: dict | None = None  # the source fact
    target: dict | None = None  # the paired target fact
    diffs: list = field(default_factory=list)
    confidence: str = "high"
    unique: bool = False  # the only leftover fact of its kind on both sides (repair needs this)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "clause": self.clause,
            "source": self.source,
            "target": self.target,
            "diffs": [
                {"path": d.path, "code": d.code, "src": d.src, "tgt": d.tgt} for d in self.diffs
            ],
            "confidence": self.confidence,
            "unique": self.unique,
        }


@dataclass
class Report:
    findings: list
    matched: list  # (fingerprint, verb) of every source fact found unchanged in the target
    source_tier: int
    target_tier: int

    @property
    def confidence(self) -> str:
        tiers = (self.source_tier, self.target_tier)
        return "low" if 3 in tiers else "medium" if 2 in tiers else "high"

    @property
    def status(self) -> str:
        if any(f.severity == ERROR for f in self.findings):
            return "fail"
        return "warn" if any(f.severity == WARNING for f in self.findings) else "pass"

    def codes(self) -> list:
        return [f.code for f in self.findings]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "source_tier": self.source_tier,
            "target_tier": self.target_tier,
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------------------------
# Values


def _hour24(t: dict):
    h, period = t["hour"], t.get("period")
    if period == "AM":
        return 0 if h == 12 else h
    if period == "PM":
        return h if h == 12 else h + 12
    return h if h == 0 or h > 12 else None


def _time_diffs(path, s, t):
    if s["minute"] != t["minute"]:
        return [Diff(path, "TIME_CHANGED", s, t)]
    hs, ht = _hour24(s), _hour24(t)
    if hs is not None and ht is not None:
        same = hs == ht
    else:
        same = s["hour"] % 12 == t["hour"] % 12
    if not same:
        return [Diff(path, "TIME_CHANGED", s, t)]
    if s.get("period") and not t.get("period") and 1 <= t["hour"] <= 12:
        return [Diff(path, "TIME_PERIOD_MISSING", s, t)]
    return []


def _date_diffs(path, s, t):
    keys = ("weekday", "day", "month", "year", "daypart")
    both = [k for k in keys if k in s and k in t]
    if any(s[k] != t[k] for k in both):
        return [Diff(path, "DATE_CHANGED", s, t)]
    if {k for k in keys if k in s} != {k for k in keys if k in t}:
        return [Diff(path, "DATE_PRECISION_CHANGED", s, t)]
    return []


def value_diffs(path: str, s, t) -> list:
    if s is None and t is None:
        return []
    if s is None:
        return [Diff(path, "FACT_ADDED", None, t)]
    if t is None:
        return [Diff(path, "FACT_MISSING", s, None)]
    st, tt = s["type"], t["type"]
    if "VAGUE" in (st, tt):
        return [] if s == t else [Diff(path, "VAGUE_QUANTITY_CHANGED", s, t)]
    if st in ("MONEY", "COUNT") and tt in ("MONEY", "COUNT"):
        out = []
        if s["amount"] != t["amount"]:
            out.append(Diff(path, "NUMBER_CHANGED", s, t))
        if st == "MONEY" and tt == "MONEY" and s["currency"] != t["currency"]:
            out.append(Diff(path, "CURRENCY_CHANGED", s, t))
        elif st == "MONEY" and tt == "COUNT":
            out.append(Diff(path, "CURRENCY_MISSING", s, t))
        elif st == "COUNT" and tt == "MONEY":
            out.append(Diff(path, "CURRENCY_ADDED", s, t))
        return out
    if st == tt:
        if st in ("PERCENT", "DURATION"):
            same = s == t
            return [] if same else [Diff(path, "NUMBER_CHANGED", s, t)]
        if st == "TIME":
            return _time_diffs(path, s, t)
        if st == "DATE":
            return _date_diffs(path, s, t)
        return [] if s == t else [Diff(path, "DATE_CHANGED", s, t)]  # DAYPART
    if "TIME" in (st, tt):
        return [Diff(path, "TIME_CHANGED", s, t)]
    if st in NUMERIC_TYPES and tt in NUMERIC_TYPES:
        return [Diff(path, "NUMBER_CHANGED", s, t)]
    return [Diff(path, "DATE_CHANGED", s, t)]


# ---------------------------------------------------------------------------------------------
# Facts


def _key(fact: dict) -> tuple:
    return (fact["fp"], fact.get("verb"))


def _group(fact: dict) -> tuple:
    kind = fact["kind"]
    if kind == "NEG":
        return (kind, fact["scope"])
    if kind == "LITERAL":
        return (kind, fact["lit"])
    return (kind,)


def fact_diffs(s: dict, t: dict) -> list:
    kind = s["kind"]
    out: list = []
    if kind == "CHANGE":
        if s["direction"] != t["direction"]:
            out.append(Diff("direction", "DIRECTION_CHANGED", s["direction"], t["direction"]))
        elif t.get("verb") != t["direction"] and s.get("verb") == s["direction"]:
            out.append(Diff("direction", "DIRECTION_CHANGED", s["direction"], t["verb"]))
    elif kind == "BOUND" and s["op"] != t["op"]:
        out.append(Diff("op", "COMPARISON_CHANGED", s["op"], t["op"]))
    elif kind == "TIMEREL" and s["rel"] != t["rel"]:
        out.append(Diff("rel", "TIMEREL_CHANGED", s["rel"], t["rel"]))
    elif kind == "LITERAL" and s["text"] != t["text"]:
        out.append(Diff("text", "LITERAL_CHANGED", s["text"], t["text"]))
    for key in VALUE_KEYS.get(kind, ()):
        out.extend(value_diffs(key, s.get(key), t.get(key)))
    if kind == "QUANT" and not out:
        if s["approx"] != t["approx"]:
            out.append(Diff("approx", "PRECISION_CHANGED", s["approx"], t["approx"]))
        elif s["role"] != t["role"]:
            out.append(Diff("role", "UNVERIFIED", s["role"], t["role"]))
    return out


def _pair_facts(src: list, tgt: list):
    """Pair leftover facts of one group by fewest differences. Returns (pairs, rest_s, rest_t)."""
    scored = []
    for i, s in enumerate(src):
        for j, t in enumerate(tgt):
            scored.append((len(fact_diffs(s, t)), i, j))
    scored.sort()
    used_s, used_t, pairs = set(), set(), []
    for _, i, j in scored:
        if i not in used_s and j not in used_t:
            used_s.add(i)
            used_t.add(j)
            pairs.append((src[i], tgt[j]))
    rest_s = [s for i, s in enumerate(src) if i not in used_s]
    rest_t = [t for j, t in enumerate(tgt) if j not in used_t]
    return pairs, rest_s, rest_t


def _flat_numbers(facts: list) -> list:
    """Tier 3: every numeric value of every fact, as plain quantities."""
    out = []

    def visit(v):
        if isinstance(v, dict) and v.get("type") in NUMERIC_TYPES:
            out.append(v)

    for f in facts:
        for key in VALUE_KEYS.get(f["kind"], ()):
            visit(f.get(key))
        target = f.get("target")
        if isinstance(target, dict) and f["kind"] == "NEG":
            visit(target)
    return [
        {"kind": "QUANT", "value": v, "role": "AMOUNT", "approx": False, "fp": "", "flags": []}
        for v in out
    ]


def _describe(v) -> str:
    if v is None:
        return "nothing"
    t = v["type"]
    if t == "MONEY":
        return f"{SYMBOL.get(v['currency'], v['currency'] + ' ')}{v['amount']}"
    if t == "PERCENT":
        return f"{v['amount']}%"
    if t == "DURATION":
        return f"{v['amount']} {v['unit'].lower()}"
    if t == "COUNT":
        return str(v["amount"])
    if t == "TIME":
        return f"{v['hour']}:{v['minute']:02d} {v.get('period') or ''}".strip()
    if t == "VAGUE":
        return "a vague amount"
    return ", ".join(f"{k} {x}" for k, x in v.items() if k != "type")


_MESSAGES = {
    "NUMBER_CHANGED": "The number changed: {s} became {t}.",
    "CURRENCY_CHANGED": "The currency changed: {s} became {t}.",
    "CURRENCY_MISSING": "The currency is missing: {s} became {t}.",
    "CURRENCY_ADDED": "The translation adds a currency: {s} became {t}.",
    "DIRECTION_CHANGED": "The direction of the change flipped: {s} became {t}.",
    "COMPARISON_CHANGED": "The comparison changed: {s} became {t}.",
    "TIMEREL_CHANGED": "The time relation changed: {s} became {t}.",
    "DATE_CHANGED": "The date changed: {s} became {t}.",
    "TIME_CHANGED": "The time changed: {s} became {t}.",
    "TIME_PERIOD_MISSING": "The translation gives no AM or PM: {s} became {t}.",
    "DATE_PRECISION_CHANGED": "The date is more or less precise: {s} became {t}.",
    "PRECISION_CHANGED": "An approximation was added or dropped.",
    "VAGUE_QUANTITY_CHANGED": "A vague amount changed: {s} became {t}.",
    "LITERAL_CHANGED": "An exact token changed: {s} became {t}.",
    "FACT_MISSING": "A fact of the source is missing from the translation: {s}.",
    "FACT_ADDED": "The translation has a fact that the source does not: {t}.",
    "UNVERIFIED": "Not checked, because the translation was only partly parsed: {s}.",
}


def _fact_text(f: dict | None) -> str:
    if f is None:
        return "nothing"
    parts = [f["kind"].lower()]
    for key in ("direction", "op", "rel", "text"):
        if key in f:
            parts.append(str(f[key]))
    for key in ("value", "from", "to", "delta", "low", "high", "anchor"):
        if f.get(key):
            parts.append(f"{key} {_describe(f[key])}")
    return " ".join(parts)


def describe_diff(d: Diff) -> tuple:
    if isinstance(d.src, dict) or isinstance(d.tgt, dict):
        return _describe(d.src), _describe(d.tgt)
    return str(d.src), str(d.tgt)


# ---------------------------------------------------------------------------------------------
# Clauses


def _counts(c: dict) -> bool:
    return bool(c["facts"] or c["cond"] or c["polarity"] == "NEG")


def _facts_of(clause: dict) -> list:
    return [f for f in clause["facts"] if not (f["kind"] == "NEG" and f["scope"] == "CLAUSE")]


def _cond_key(c: dict):
    return (c["cond"]["op"], c["cond"]["role"]) if c["cond"] else None


def _align(src: list, tgt: list):
    """Pair clauses: equal fingerprints first, then the same conditional role, then order."""
    pairs, rs, rt = [], list(src), list(tgt)
    for s in list(rs):
        for t in rt:
            if s["fp"] == t["fp"]:
                pairs.append((s, t))
                rs.remove(s)
                rt.remove(t)
                break
    for s in list(rs):
        for t in rt:
            if _cond_key(s) == _cond_key(t) and s["polarity"] == t["polarity"]:
                pairs.append((s, t))
                rs.remove(s)
                rt.remove(t)
                break
    for s in list(rs):
        for t in rt:
            if _cond_key(s) == _cond_key(t):
                pairs.append((s, t))
                rs.remove(s)
                rt.remove(t)
                break
    while rs and rt:
        pairs.append((rs.pop(0), rt.pop(0)))
    pairs.sort(key=lambda p: p[0]["index"])
    return pairs, rs, rt


# ---------------------------------------------------------------------------------------------
# Verify


class _Run:
    def __init__(self, source: IR, target: IR):
        self.source, self.target = source, target
        self.findings: list = []
        self.matched: list = []
        self.tier_flag = 3 if 3 in (source.tier, target.tier) else 0
        self.partial = 2 in (source.tier, target.tier)

    # -- emit ------------------------------------------------------------------------------
    def add(
        self,
        code,
        message,
        clause=None,
        source=None,
        target=None,
        diffs=(),
        severity=None,
        unique=False,
    ):
        confidence = "low" if self.tier_flag else "medium" if self.partial else "high"
        self.findings.append(
            Finding(
                code=code,
                severity=severity or SEVERITY[code],
                message=message,
                clause=clause,
                source=source,
                target=target,
                diffs=list(diffs),
                confidence=confidence,
                unique=unique,
            )
        )

    def missing(self, fact, clause):
        if self.tier_flag:
            if fact["kind"] == "QUANT":
                self.add(
                    "FACT_MISSING",
                    _MESSAGES["FACT_MISSING"].format(s=_fact_text(fact)),
                    clause,
                    fact,
                    severity=WARNING,
                )
            return
        if self.partial:
            self.add("UNVERIFIED", _MESSAGES["UNVERIFIED"].format(s=_fact_text(fact)), clause, fact)
            return
        if fact["kind"] == "LITERAL" and fact["lit"] == "ORDINAL":
            self.add("UNVERIFIED", "An ordinal was not found in the translation.", clause, fact)
            return
        if fact["kind"] == "NEG":
            self.add("NEGATION_DROPPED", "A negation of the source is missing.", clause, fact)
            return
        self.add("FACT_MISSING", _MESSAGES["FACT_MISSING"].format(s=_fact_text(fact)), clause, fact)

    def extra(self, fact, clause):
        if self.source.tier == 3 and fact["kind"] != "QUANT":
            return
        if fact["kind"] == "LITERAL" and fact["lit"] == "ORDINAL":
            return
        if fact["kind"] == "NEG":
            self.add("NEGATION_ADDED", "The translation adds a negation.", clause, None, fact)
            return
        self.add(
            "FACT_ADDED", _MESSAGES["FACT_ADDED"].format(t=_fact_text(fact)), clause, None, fact
        )

    # -- facts -----------------------------------------------------------------------------
    def compare_facts(self, src: list, tgt: list, clause):
        if self.tier_flag:
            src, tgt = _flat_numbers(src), _flat_numbers(tgt)
        rs, rt = list(src), list(tgt)
        for s in list(rs):  # exact matches
            for t in rt:
                if _key(s) == _key(t) and s["fp"]:
                    self.matched.append(_key(s))
                    self.check_notation(s, t, clause)
                    rs.remove(s)
                    rt.remove(t)
                    break
        if self.tier_flag:  # flattened numbers have no fingerprint: compare the values
            for s in list(rs):
                for t in rt:
                    if s["value"] == t["value"]:
                        rs.remove(s)
                        rt.remove(t)
                        break
        groups: dict = {}
        for f in rs:
            groups.setdefault(_group(f), ([], []))[0].append(f)
        for f in rt:
            groups.setdefault(_group(f), ([], []))[1].append(f)
        for gs, gt in groups.values():
            pairs, only_s, only_t = _pair_facts(gs, gt)
            for s, t in pairs:
                self.report_pair(s, t, clause, unique=len(gs) == 1 and len(gt) == 1)
            for s in only_s:
                self.missing(s, clause)
            for t in only_t:
                self.extra(t, clause)

    def check_notation(self, s, t, clause):
        """Equal values written with different ambiguous notations ('1.500' and '1,5')."""
        a, b = set(s["flags"]) & NUMBER_FLAGS, set(t["flags"]) & NUMBER_FLAGS
        if a and b and a != b:
            self.add(
                "AMBIGUOUS_NUMBER",
                "The two numbers are equal only if their separators mean the same in both texts.",
                clause,
                s,
                t,
            )

    def report_pair(self, s, t, clause, unique=False):
        diffs = fact_diffs(s, t)
        if not diffs:
            self.matched.append(_key(s))
            return
        code = next(c for c in PRECEDENCE if any(d.code == c for d in diffs))
        if code in NUMBERISH and (set(s["flags"]) | set(t["flags"])) & NUMBER_FLAGS:
            self.add(
                "AMBIGUOUS_NUMBER",
                "A number may be written with a comma or dot that has another meaning here.",
                clause,
                s,
                t,
                diffs,
            )
            return
        if code == "FACT_MISSING" and self.partial:
            code = "UNVERIFIED"
        shown = next(d for d in diffs if d.code == code)
        a, b = describe_diff(shown)
        self.add(code, _MESSAGES[code].format(s=a, t=b), clause, s, t, diffs, unique=unique)

    # -- clauses ---------------------------------------------------------------------------
    def run(self) -> Report:
        src = [c for c in self.source.clauses if _counts(c)]
        tgt = [c for c in self.target.clauses if _counts(c)]
        self.source_inconsistency()
        pairs, rest_s, rest_t = _align(src, tgt)
        # A clause whose facts were all lost pairs with an empty clause of the other side.
        empty_t = [c for c in self.target.clauses if not _counts(c)]
        empty_s = [c for c in self.source.clauses if not _counts(c)]
        while rest_s and empty_t:
            pairs.append((rest_s.pop(0), empty_t.pop(0)))
        while rest_t and empty_s:
            pairs.append((empty_s.pop(0), rest_t.pop(0)))
        if not rest_s and not rest_t:
            pairs.sort(key=lambda p: p[0]["index"])
            for s, t in pairs:
                self.compare_clause(s, t)
        else:
            self.add(
                "CLAUSE_COUNT_CHANGED",
                f"The source has {len(src)} clauses and the translation has {len(tgt)}.",
            )
            sneg = sum(c["polarity"] == "NEG" for c in src)
            tneg = sum(c["polarity"] == "NEG" for c in tgt)
            if sneg > tneg:
                self.add("NEGATION_DROPPED", "A negated clause of the source has no negation.")
            elif tneg > sneg:
                self.add("NEGATION_ADDED", "The translation negates a clause the source does not.")
            self.compare_facts(
                [f for c in src for f in _facts_of(c)], [f for c in tgt for f in _facts_of(c)], None
            )
        self.findings.sort(key=lambda f: (f.clause is None, f.clause or 0))
        return Report(self.findings, self.matched, self.source.tier, self.target.tier)

    def source_inconsistency(self):
        for c in self.source.clauses:
            for f in c["facts"]:
                if "SOURCE_INCONSISTENT" in f["flags"]:
                    self.add(
                        "SOURCE_INCONSISTENT",
                        "The source wording contradicts its own numbers; the numbers were trusted.",
                        c["index"],
                        f,
                    )

    def compare_clause(self, s, t):
        ci = s["index"]
        if s["polarity"] != t["polarity"]:
            uncertain = "DOUBLE_NEG" in s["flags"] or "DOUBLE_NEG" in t["flags"]
            if uncertain:
                self.add(
                    "NEGATION_UNCERTAIN", "Two negations cancel out; the polarity is uncertain.", ci
                )
            elif s["polarity"] == "NEG":
                self.add(
                    "NEGATION_DROPPED",
                    "The source negates this clause; the translation does not.",
                    ci,
                )
            else:
                self.add(
                    "NEGATION_ADDED",
                    "The translation negates this clause; the source does not.",
                    ci,
                )
        if (
            _cond_key(s) != _cond_key(t)
            and (s["cond"] or t["cond"])
            and s["polarity"] == t["polarity"]
        ):
            self.add("CONDITION_CHANGED", "The conditional structure of the clause changed.", ci)
        self.compare_facts(_facts_of(s), _facts_of(t), ci)


def verify(source: IR, target: IR) -> Report:
    """Compare the IR of the source sentence with the IR of its translation."""
    return _Run(source, target).run()


# ---------------------------------------------------------------------------------------------
# Tone (spec 08): advisory only. These findings never fail a check and never trigger repair.


def _tone(code: str, message: str) -> Finding:
    return Finding(code, SEVERITY[code], message, confidence="low")


def compare_tone(src: dict | None, tgt: dict | None) -> list:
    """Tone findings for a pair of emotion records (``emotion.EmotionAnalyzer`` output)."""
    if not src or not tgt:
        return []
    s, t = src["sentiment"], tgt["sentiment"]
    polar = ("positive", "negative")
    sarcastic = bool(src.get("possible_sarcasm"))
    if sarcastic:
        # Its surface sentiment is not its meaning: only the loss of the irony is checked.
        if t == "positive":
            return [
                _tone(
                    "SARCASM_POSSIBLY_LOST",
                    "The source sounds sarcastic but the translation reads as sincerely positive.",
                )
            ]
        return []
    if s in polar and t in polar and s != t:
        return [
            _tone(
                "TONE_POLARITY_FLIPPED",
                f"The source reads {s} but the translation reads {t}.",
            )
        ]
    if s == "neutral" and t in polar and tgt["confidence"] >= TONE_STRONG:
        return [_tone("TONE_INTENSIFIED", f"The source is neutral but the translation reads {t}.")]
    if s in polar and t == "neutral" and tgt["confidence"] >= TONE_STRONG:
        return [_tone("TONE_WEAKENED", f"The source reads {s} but the translation reads neutral.")]
    if s in polar and t == "uncertain":
        return [_tone("TONE_UNCERTAIN", "The tone of the translation could not be determined.")]
    a, b = src["label"], tgt["label"]
    if s == t and a != b and "neutral" not in (a, b) and "uncertain" not in (a, b):
        return [_tone("EMOTION_CHANGED", f"The emotion changes from {a} to {b}.")]
    return []
