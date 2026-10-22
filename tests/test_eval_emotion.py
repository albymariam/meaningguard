"""The emotion evaluation tool: sheet reading, kappa, scoring (no models)."""

import importlib.util
import sys
from pathlib import Path

import pytest

path = Path(__file__).resolve().parent.parent / "tools" / "eval_emotion.py"
spec = importlib.util.spec_from_file_location("eval_emotion", path)
ev = importlib.util.module_from_spec(spec)
sys.modules["eval_emotion"] = ev
spec.loader.exec_module(ev)

HEAD = "id,sentence,proposed_emotion,proposed_sarcasm,your_emotion,your_sarcasm (Y/N),notes\n"


def sheet(tmp_path, name, lines):
    p = tmp_path / name
    p.write_text("﻿" + HEAD + "".join(lines), "utf-8")
    return p


def test_read_sheet(tmp_path):
    p = sheet(
        tmp_path, "a.csv", ["s1,x,anger,,Anger,y,\n", "s2,x,joy,,,,\n", "s3,x,fear,,fear,N,\n"]
    )
    assert ev.read_sheet(p) == {"s1": ("anger", True), "s2": (None, None), "s3": ("fear", False)}


def test_read_sheet_rejects_bad_values(tmp_path):
    with pytest.raises(ValueError, match="unknown emotion"):
        ev.read_sheet(sheet(tmp_path, "b.csv", ["s1,x,anger,,rage,,\n"]))
    with pytest.raises(ValueError, match="Y or N"):
        ev.read_sheet(sheet(tmp_path, "c.csv", ["s1,x,anger,,anger,maybe,\n"]))


def test_kappa():
    assert ev.kappa([("a", "a"), ("b", "b")]) == 1.0
    assert ev.kappa([("a", "b"), ("b", "a")]) == -1.0
    assert ev.kappa([]) is None and ev.kappa([("a", "a")]) is None  # no chance-corrected value


def test_prf():
    assert ev.prf(2, 1, 2) == pytest.approx(
        {"precision": 2 / 3, "recall": 0.5, "f1": 4 / 7, "tp": 2, "fp": 1, "fn": 2}
    )
    assert ev.prf(0, 0, 0)["f1"] is None


SENTENCES = [
    {"id": f"s{i}", "text": t, "emotion": e, "sarcasm": sa, "split": sp}
    for i, (t, e, sa, sp) in enumerate(
        [("a", "anger", True, "dev"), ("b", "joy", False, "dev"), ("c", "fear", None, "heldout")]
    )
] + [{"id": "s9", "text": "z", "emotion": None, "sarcasm": None, "split": "dev"}]


def test_gold_rows_need_labels_or_the_flag(tmp_path):
    assert ev.gold_rows(SENTENCES, [], False, "dev") == ([], [])
    rows, notes = ev.gold_rows(SENTENCES, [], True, "dev")
    assert [r["id"] for r in rows] == ["s0", "s1"] and notes == []
    mine = {"s0": ("sadness", False), "s1": (None, None)}
    rows, _ = ev.gold_rows(SENTENCES, [mine], False, "dev")
    assert [(r["id"], r["emotion"]) for r in rows] == [("s0", "sadness")]


def test_only_agreed_rows_count_with_two_annotators():
    a = {"s0": ("anger", True), "s1": ("joy", False)}
    b = {"s0": ("anger", True), "s1": ("fear", False)}
    rows, notes = ev.gold_rows(SENTENCES, [a, b], False, "dev")
    assert [r["id"] for r in rows] == ["s0"] and "agree on 1 of 2" in notes[0]


def rec(label, sentiment, sarcasm=False, signals=()):
    return {
        "label": label,
        "sentiment": sentiment,
        "possible_sarcasm": sarcasm,
        "signals": list(signals),
    }


def test_evaluate():
    rows = [
        {"id": "a", "text": "A", "emotion": "anger", "sarcasm": True},
        {"id": "b", "text": "B", "emotion": "joy", "sarcasm": False},
        {"id": "c", "text": "C", "emotion": "fear", "sarcasm": False},
        {"id": "d", "text": "D", "emotion": "surprise", "sarcasm": None},
    ]
    out = {
        "A": rec("anger", "negative", True, [{"name": "m", "sentiment": "negative"}]),
        "B": rec("sadness", "negative", False, [{"name": "m", "sentiment": "negative"}]),
        "C": rec("uncertain", "uncertain", True),
        "D": rec("surprise", "uncertain"),
    }
    res = ev.evaluate(rows, out.__getitem__)
    assert res["label"] == {"accuracy_when_labelled": round(2 / 3, 3), "coverage": 0.75}
    assert res["sentiment"]["accuracy"] == round(1 / 3, 3)  # surprise has no gold sentiment
    assert res["sentiment"]["uncertain_rate"] == 0.5
    assert res["sentiment"]["single_signal"] == {"m": 0.5}
    assert (res["sarcasm"]["tp"], res["sarcasm"]["fp"], res["sarcasm"]["fn"]) == (1, 1, 0)
    assert res["confusion"]["anger->anger"] == 1 and res["confusion"]["fear->uncertain"] == 1
    assert [m[0] for m in res["sentiment_mistakes"]] == ["b", "c"]


def test_evaluate_target_side():
    rows = [{"id": "a", "text": "A", "emotion": "anger", "sarcasm": None}]
    seen = []

    def target(hindi, english):
        seen.append((hindi, english))
        return {"sentiment": "negative"}

    res = ev.evaluate(rows, lambda t: rec("anger", "negative"), target, {"a": ("हि", "en")})
    assert seen == [("हि", "en")] and res["target"] == {
        "rows": 1,
        "accuracy": 1.0,
        "uncertain_rate": 0.0,
    }
    assert ev.evaluate(rows, lambda t: rec("anger", "negative"), target, {})["target"]["rows"] == 0


def test_heldout_is_refused_without_the_flag(capsys):
    assert ev.main(["--split", "heldout"]) == 2
    assert "once" in capsys.readouterr().out


def test_no_gold_is_reported(tmp_path, capsys):
    empty = sheet(tmp_path, "e.csv", ["s1,x,anger,,,,\n"])
    assert ev.main(["--labels", str(empty)]) == 1
    assert "no gold labels" in capsys.readouterr().out
