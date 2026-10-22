"""IR invariants (spec 05 section 9), fingerprint equality (02 section 6), robustness."""

import json
import re
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from meaningguard import diagnostics
from meaningguard.ir import IR, IRFormatError, compile_text
from meaningguard.lexer import EmptyInput, InputTooLong

ROOT = Path(__file__).resolve().parent.parent
SENTENCES = [
    json.loads(x) for x in (ROOT / "data" / "sentences.jsonl").read_text("utf-8").splitlines()
]
PAIRS = [
    json.loads(x) for x in (ROOT / "data" / "corruptions.jsonl").read_text("utf-8").splitlines()
]
DEV = [r for r in SENTENCES if r["split"] == "dev"]
PARSER_ROWS = [
    json.loads(x)
    for x in (ROOT / "tests" / "spec_cases" / "parser.jsonl").read_text("utf-8").splitlines()
]
HEX16 = re.compile(r"^[0-9a-f]{16}$")


def all_inputs():
    for row in PARSER_ROWS:
        yield row["input"], row["lang"]
    for row in SENTENCES:
        yield row["text"], "en"
    for pair in PAIRS:
        yield pair["source"], "en"
        yield pair["reference"], "hi"
        yield pair["target"], "hi"


INPUTS = sorted(set(all_inputs()))


def walk_facts(fact):
    yield fact
    target = fact.get("target")
    if isinstance(target, dict) and "kind" in target:
        yield from walk_facts(target)


# ---------------------------------------------------------------------------------------------
# Invariants of section 9


@pytest.mark.parametrize("text,lang", INPUTS)
def test_round_trip(text, lang):
    ir = compile_text(text, lang)
    again = IR.from_json(ir.to_json())
    assert again.to_dict() == ir.to_dict()
    assert json.loads(ir.to_json()) == ir.to_dict()


@pytest.mark.parametrize("text,lang", INPUTS)
def test_structure(text, lang):
    ir = compile_text(text, lang)
    assert HEX16.match(ir.fp)
    indices = [c["index"] for c in ir.clauses]
    assert indices == list(range(len(indices)))
    for clause in ir.clauses:
        assert clause["polarity"] in ("POS", "NEG") and HEX16.match(clause["fp"])
        cond = clause["cond"]
        if cond and cond["peer"] is not None:
            assert cond["peer"] in indices and cond["peer"] != clause["index"]
        for fact in clause["facts"]:
            assert HEX16.match(fact["fp"])
            a, b = fact["span"]
            assert 0 <= a <= b <= len(ir.text)
    for d in ir.diagnostics:
        assert d["code"] in diagnostics.CODES and d["severity"] in ("info", "warning", "error")
        if d["span"]:
            assert 0 <= d["span"][0] <= d["span"][1] <= len(ir.text)


@pytest.mark.parametrize("text,lang", INPUTS)
def test_nested_values_are_not_duplicated_as_quants(text, lang):
    """Invariant 3: a value inside CHANGE, RANGE, BOUND or TIMEREL is stored there only."""
    ir = compile_text(text, lang)
    for clause in ir.clauses:
        quant_spans = [f["span"] for f in clause["facts"] if f["kind"] == "QUANT"]
        for fact in clause["facts"]:
            if fact["kind"] in ("CHANGE", "RANGE", "BOUND", "TIMEREL"):
                for qs in quant_spans:
                    assert not (fact["span"][0] <= qs[0] and qs[1] <= fact["span"][1])


@pytest.mark.parametrize("text,lang", INPUTS)
def test_tier_three_only_has_quants_and_clause_negations(text, lang):
    ir = compile_text(text, lang)
    if ir.tier == 3:
        for fact in ir.facts():
            assert fact["kind"] == "QUANT" or (fact["kind"] == "NEG" and fact["scope"] == "CLAUSE")


def test_every_diagnostic_code_is_in_the_spec():
    spec = (ROOT / "docs" / "spec" / "07_parser.md").read_text("utf-8")
    for code in diagnostics.CODES:
        assert f"`{code}`" in spec, code


# ---------------------------------------------------------------------------------------------
# Fingerprints


def fp(text, lang="en"):
    return compile_text(text, lang).fp


SAME = [
    # surface forms of one amount, across scripts and languages
    ["Pay ₹5 lakh.", "Pay ₹500000.", "Pay 5,00,000 rupees.", "Pay five lakh rupees.", "₹५ लाख दें।"],
    ["Pay Rs. 1,250.", "Pay ₹1250.", "Pay INR 1250."],
    ["Pay ₹500 or ₹600.", "Pay ₹600 or ₹500."],  # C-ORDER
    ["The price fell from ₹500 to ₹400.", "कीमत ₹५०० से घटकर ₹४०० हो गई।"],
    ["Do not submit after Friday.", "शुक्रवार के बाद जमा न करें।"],
    ["Submit not after Friday.", "Submit by Friday.", "शुक्रवार तक जमा करें।"],
    ["Pay more than ₹500.", "₹५०० से ज़्यादा भुगतान करें।"],
    [
        "Deposit between ₹5 lakh and ₹10 lakh.",
        "Deposit ₹5-10 lakh.",
        "जमा राशि ₹5 लाख और ₹10 लाख के बीच रखें।",
    ],
    [
        "Unless you pay by Monday, the account will be closed.",
        "यदि आप सोमवार तक भुगतान नहीं करते हैं, तो खाता बंद कर दिया जाएगा।",
    ],
    ["Complete it within 7 days.", "7 दिनों में पूरा करें।"],
    ["The meeting starts at 5 PM.", "बैठक शाम 5 बजे शुरू होगी।"],
    ["Prices rose by 12.5%.", "कीमतें 12.5% बढ़ीं।"],
]
DIFFERENT = [
    ("Pay ₹500.", "Pay $500."),
    ("Pay ₹500.", "Pay ₹5000."),
    ("Pay ₹500.", "Do not pay ₹500."),
    ("The price fell to ₹400.", "The price rose to ₹400."),
    ("Pay more than ₹500.", "Pay at least ₹500."),
    ("Submit before Friday.", "Submit after Friday."),
    ("Submit by Friday.", "Submit by Thursday."),
    ("Pay within 7 days.", "Pay within 7 weeks."),
    ("Meet at 5 AM.", "Meet at 5 PM."),
    ("If you pay, ₹500 is refunded.", "Unless you pay, ₹500 is refunded."),
    ("Pay ₹500 or ₹600.", "Pay ₹500 or ₹700."),
    ("Pay ₹500 and ₹600.", "Pay ₹500."),
]


@pytest.mark.parametrize("group", SAME, ids=lambda g: g[0][:30])
def test_same_meaning_gives_the_same_fingerprint(group):
    prints = {fp(t, "hi" if re.search(r"[ऀ-ॿ]", t) else "en") for t in group}
    assert len(prints) == 1, group


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_different_meaning_gives_different_fingerprints(a, b):
    assert fp(a) != fp(b)


def test_decimal_spellings_share_one_fingerprint():
    from decimal import Decimal

    from meaningguard.nodes import dec_str

    assert fp("Pay ₹500000.") == fp("Pay ₹500000.00.") == fp("Pay ₹0500000.")
    assert {dec_str(Decimal(x)) for x in ("500000", "5E+5", "500000.00", "0500000")} == {"500000"}


def test_surface_words_and_positions_are_excluded():
    assert fp("Please kindly pay ₹500 right now.") == fp("₹500 pay.")


def test_clause_order_does_not_change_the_sentence_fingerprint():
    assert fp("If it rains, pay ₹500.") == fp("Pay ₹500 if it rains.")


# ---------------------------------------------------------------------------------------------
# Dev data: gold annotations and source/reference pairs (held-out is not touched here)


def values(fact):
    for key in ("value", "from", "to", "delta", "low", "high", "anchor"):
        v = fact.get(key)
        if isinstance(v, dict) and "type" in v:
            yield v
    target = fact.get("target")
    if isinstance(target, dict):
        if "kind" in target:
            yield from values(target)
        elif "type" in target:
            yield target


def my_rels(ir):
    rels = []
    for clause in ir.clauses:
        for f in clause["facts"]:
            k = f["kind"]
            if k == "CHANGE":
                rels.append("CHANGE:" + f["direction"])
            elif k == "BOUND":
                rels.append("BOUND:" + f["op"])
            elif k == "TIMEREL":
                rels.append("TIMEREL:" + f["rel"])
            elif k == "NEG":
                rels.append("NEG:QUANT" if f["scope"] == "QUANT" else "NEG")
            elif k == "RANGE":
                rels.append("RANGE")
        if "DOUBLE_NEG" in clause["flags"]:
            rels.append("DOUBLE_NEG")
        cond = clause["cond"]
        if cond and cond["op"] == "ELSE":
            rels.append("COND:ELSE")
        elif cond and cond["role"] == "ANTECEDENT":
            rels.append("COND:UNLESS" if "UNLESS_REWRITTEN" in clause["flags"] else "COND:IF")
    return rels


@pytest.mark.parametrize("row", DEV, ids=lambda r: r["id"])
def test_dev_gold(row):
    """TIMEREL:ON is left out of the comparison: the gold lists it for 'on Monday' but not for
    'at 6 PM', and times and dates are counted in their own fields."""
    ir = compile_text(row["text"], "en")
    vs = [v for f in ir.facts() for v in values(f)]
    quantity_types = ("MONEY", "COUNT", "PERCENT", "DURATION")
    mine = sorted(
        (v["type"], v["amount"], v.get("currency") or v.get("unit"))
        for v in vs
        if v["type"] in quantity_types
    )
    gold = sorted(
        (q["type"], q["value"], q.get("currency") or q.get("unit"))
        for q in row["gold"]["quantities"]
    )
    assert mine == gold
    want = sorted(r for r in row["gold"]["rels"] if r != "TIMEREL:ON")
    assert sorted(r for r in my_rels(ir) if r != "TIMEREL:ON") == want
    assert sum(v["type"] == "TIME" for v in vs) == len(row["gold"]["times"])
    assert sum(v["type"] == "DATE" for v in vs) == len(row["gold"]["dates"])
    assert sum(v["type"] == "VAGUE" for v in vs) == row["gold"]["vague"]


DEV_IDS = {r["id"] for r in DEV}
DEV_PAIRS = [p for p in PAIRS if p["source_id"] in DEV_IDS]


@pytest.mark.parametrize("pair", DEV_PAIRS, ids=lambda p: p["id"])
def test_dev_pairs(pair):
    """The Hindi reference agrees with the English source; a control agrees; a corruption does not.
    (The Hindi in corruptions.jsonl is written by the author and not yet reviewed.)"""
    src = compile_text(pair["source"], "en")
    ref = compile_text(pair["reference"], "hi")
    tgt = compile_text(pair["target"], "hi")
    assert ref.fp == src.fp
    inconsistent = "SOURCE_INCONSISTENT"
    same = tgt.fp == src.fp and (inconsistent in tgt.codes()) == (inconsistent in src.codes())
    assert same == (pair["kind"] == "control")


# ---------------------------------------------------------------------------------------------
# Errors and robustness


def test_bad_input_is_rejected_by_the_lexer():
    with pytest.raises(EmptyInput):
        compile_text("   ", "en")
    with pytest.raises(InputTooLong):
        compile_text("a" * 1001, "en")
    with pytest.raises(ValueError):
        compile_text("Pay ₹5.", "fr")


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"schema": "mgir/2"},
        {"schema": "mgir/1"},
        {"schema": "mgir/1", "lang": "fr", "text": "", "tier": 1, "clauses": [], "diagnostics": []},
        {"schema": "mgir/1", "lang": "en", "text": "", "tier": 4, "clauses": [], "diagnostics": []},
    ],
)
def test_malformed_ir_is_rejected(data):
    with pytest.raises(IRFormatError):
        IR.from_dict(data)


def test_internal_error_falls_back_to_lexer_facts(monkeypatch):
    from meaningguard import parser

    def boom(self):
        raise RuntimeError("forced")

    monkeypatch.setattr(parser.ClauseParser, "parse", boom)
    ir = compile_text("Do not pay ₹500.", "en")
    assert ir.tier == 3
    assert {f["kind"] for f in ir.facts()} == {"QUANT", "NEG"}
    assert "INTERNAL_PARSE_ERROR" in ir.codes()


WORDS = [
    "from",
    "to",
    "between",
    "and",
    "more than",
    "at least",
    "or more",
    "up to",
    "about",
    "rose",
    "fell",
    "by",
    "increase",
    "if",
    "unless",
    "then",
    "otherwise",
    "not",
    "no",
    "never",
    "without",
    "before",
    "after",
    "until",
    "within",
    "on",
    "at",
    "since",
    "Friday",
    "June",
    "5",
    "10",
    "2026",
    "₹",
    "$",
    "%",
    "lakh",
    "crore",
    "hundred",
    "five",
    "दो",
    "सवा",
    "AM",
    "PM",
    "days",
    "morning",
    "-",
    ",",
    ";",
    ".",
    "?",
    "।",
    "से",
    "तक",
    "के बाद",
    "से पहले",
    "नहीं",
    "न",
    "मत",
    "अगर",
    "तो",
    "वरना",
    "जब तक",
    "बढ़कर",
    "गिरकर",
    "हो गई",
    "ज़्यादा",
    "कम से कम",
    "या",
    "और",
    "के बीच",
    "रुपये",
    "५",
    "१०",
    "शुक्रवार",
    "बजे",
    "दिन",
    "x",
    "price",
    "कीमत",
    "A5",
    "12/10/2026",
    "+91 98765 43210",
    "1,00,000",
    "½",
    "(",
    ")",
    "'",
    '"',
    "‍",
    "\t",
]


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(WORDS), min_size=1, max_size=40), st.sampled_from(["en", "hi"]))
def test_fuzz_never_crashes_and_always_round_trips(tokens, lang):
    text = " ".join(tokens)[:1000]
    if not text.strip():
        return
    try:
        ir = compile_text(text, lang)
    except EmptyInput:  # only invisible characters
        return
    assert IR.from_json(ir.to_json()).to_dict() == ir.to_dict()
    assert compile_text(text, lang).to_dict() == ir.to_dict()  # deterministic
    assert "INTERNAL_PARSE_ERROR" not in ir.codes(), text
    if ir.tier == 3:
        for fact in ir.facts():
            assert fact["kind"] == "QUANT" or (fact["kind"] == "NEG" and fact["scope"] == "CLAUSE")


@settings(max_examples=200, deadline=None)
@given(st.text(min_size=1, max_size=300), st.sampled_from(["en", "hi"]))
def test_fuzz_arbitrary_text(text, lang):
    if not text.strip():
        return
    try:
        ir = compile_text(text, lang)
    except EmptyInput:
        return
    assert "INTERNAL_PARSE_ERROR" not in ir.codes(), repr(text)
    assert IR.from_json(ir.to_json()).to_dict() == ir.to_dict()
