"""The verifier evaluation tool: baselines, mutations, gates (no models)."""

import importlib.util
import json
import sys
from pathlib import Path

path = Path(__file__).resolve().parent.parent / "tools" / "eval_verifier.py"
spec = importlib.util.spec_from_file_location("eval_verifier", path)
ev = importlib.util.module_from_spec(spec)
sys.modules["eval_verifier"] = ev
spec.loader.exec_module(ev)

ROW = {"gold": {"quantities": [{"currency": "INR"}], "rels": ["NEG"]}}


def test_digits_baseline_reads_devanagari_and_grouping():
    assert ev.digits_of("१५,००० रुपये") == (("15000",), ())
    assert ev.digits_of("₹500") != ev.digits_of("$500")


def test_mutations_change_one_thing():
    out = {k: (t, c) for k, t, c in ev.mutate(ROW, "शुल्क ₹५०० है, वह नहीं आया")}
    assert "५०१" in out["digit"][0]
    assert "$" in out["currency"][0] and out["currency"][1] == "CURRENCY_CHANGED"
    assert "नहीं" not in out["negation"][0]
    assert ev.mutate({"gold": {"quantities": [], "rels": []}}, "कुछ नहीं") == []


def test_hand_set_is_caught_by_the_verifier():
    sentences = {r["id"]: r for r in ev.read_jsonl(ev.ROOT / "data" / "sentences.jsonl")}
    cases = ev.hand_cases(sentences, "dev")
    result = ev.score(cases)
    assert result["none"]["recall"] == 0
    assert result["full"]["recall"] >= result["digits"]["recall"]
    assert result["full"]["false_alarm_rate"] == 0


def test_heldout_needs_the_flag(tmp_path, capsys):
    assert ev.main(["--split", "heldout", "--out", str(tmp_path / "o.json")]) == 2


def test_main_writes_report(tmp_path):
    out = tmp_path / "r.json"
    assert ev.main(["--out", str(out)]) == 0
    assert set(json.loads(out.read_text())["hand"]["systems"]) == {
        "none",
        "digits",
        "no-canon",
        "full",
    }


def test_canonicalization_ablation():
    out = ev.paraphrase_false_alarms()
    assert out["full"]["flagged"] == 0
    assert out["no-canon"]["flagged"] > out["full"]["flagged"]
    assert out["none"]["flagged"] == 0
