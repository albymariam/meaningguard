"""Real IndicTrans2 output for dev sentences: English and Hindi IRs agree, or disagree for a reason.

The four disagreements are correct reports: s032 garbled number word, s037 'बावन सौ' (5200),
s039 'से अधिक' (more than), and s118 verb ellipsis in 'not only ... but also' (known limit).
"""

import json
from pathlib import Path

import pytest

from meaningguard.ir import compile_text

ROWS = [
    json.loads(x)
    for x in (Path(__file__).parent / "hindi_pairs.jsonl").read_text("utf-8").splitlines()
]


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_pair(row):
    same = compile_text(row["en"], "en").fp == compile_text(row["hi"], "hi").fp
    assert same == row["agree"]


@pytest.mark.parametrize(
    "text,kinds",
    [
        ("कुछ लोग आए।", ["QUANT"]),  # 'some people' stays a vague amount
        ("कुछ भी न दें।", ["NEG"]),  # 'कुछ भी' is 'anything'
        ("एक लाख रुपये दें।", ["QUANT"]),  # 'एक' with a scale is a number
        ("5 बजे के बाद काम नहीं है।", ["NEG", "TIMEREL"]),  # only bounds take the wider NEG scope
    ],
)
def test_new_hindi_rules_do_not_overreach(text, kinds):
    assert sorted(f["kind"] for f in compile_text(text, "hi").facts()) == kinds


@pytest.mark.parametrize(
    "text", ["Meet on March 3rd.", "Meet on the 3rd of March.", "Meet on 3rd March."]
)
def test_english_ordinal_day_joins_the_date(text):
    kinds = [f["kind"] for f in compile_text(text, "en").facts()]
    assert kinds == ["TIMEREL"]
