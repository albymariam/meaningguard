"""Does the verifier catch corrupted translations? Compared with two baselines (no models needed).

    python tools/eval_verifier.py                       # dev rows
    python tools/eval_verifier.py --mt data/translations.jsonl   # also corrupt real MT output
    python tools/eval_verifier.py --split heldout --i-know-this-spends-heldout

Systems: ``none`` accepts everything; ``digits`` compares the digit strings and currency signs of
the two texts; ``full`` is the
verifier. Positives are corrupted targets, negatives are controls
(hand-made) or the untouched MT output. Real MT output has no gold, so its flag rate is reported
as "flagged", not as false alarms: review ``flagged_mt`` in the JSON by hand.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meaningguard.ir import compile_text  # noqa: E402
from meaningguard.repair import repair  # noqa: E402

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
NUMBER = re.compile(r"[0-9०-९]+(?:[.,][0-9०-९]+)*")
SIGNS = "₹$€£"
SYSTEMS = ("none", "digits", "full")


def read_jsonl(path: Path) -> list:
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def digits_of(text: str) -> tuple:
    nums = [m.group().translate(DEVANAGARI_DIGITS).replace(",", "") for m in NUMBER.finditer(text)]
    return tuple(sorted(nums)), tuple(sorted(c for c in text if c in SIGNS))


def flags(system: str, source: str, target: str) -> tuple:
    """(flagged, codes) of one system on one pair."""
    if system == "none":
        return False, []
    if system == "digits":
        return digits_of(source) != digits_of(target), []
    report = repair(compile_text(source, "en"), target, apply=False).before
    return report.status != "pass", report.codes()


def mutate(row: dict, hindi: str) -> list:
    """Corruptions of one MT output with a known cause: (kind, corrupted text, expected code)."""
    out = []
    gold = row["gold"]
    nums = list(NUMBER.finditer(hindi))
    if gold["quantities"] and nums:
        m = nums[-1]
        digits = m.group()
        i = max(k for k, c in enumerate(digits) if c.isdigit())
        zero = "०" if digits[i] in "०१२३४५६७८९" else "0"
        new = chr(ord(zero) + (ord(digits[i]) - ord(zero) + 1) % 10)
        out.append(
            ("digit", hindi[: m.start() + i] + new + hindi[m.start() + i + 1 :], "NUMBER_CHANGED")
        )
    if any(q.get("currency") == "INR" for q in gold["quantities"]) and "₹" in hindi:
        out.append(("currency", hindi.replace("₹", "$"), "CURRENCY_CHANGED"))
    if "NEG" in gold["rels"] and "नहीं" in hindi:
        out.append(("negation", re.sub(r"\s*नहीं", "", hindi, count=1), "NEGATION_DROPPED"))
    return out


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else None
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn}


def score(cases: list) -> dict:
    """``cases``: dicts with source, target, positive, expected (list), repairable, tag."""
    result = {}
    for system in SYSTEMS:
        tp = fp = fn = tn = right = 0
        by_kind: Counter = Counter()
        seen: Counter = Counter()
        for c in cases:
            flagged, codes = flags(system, c["source"], c["target"])
            if c["positive"]:
                tp, fn = tp + flagged, fn + (not flagged)
                seen[c["tag"]] += 1
                by_kind[c["tag"]] += flagged
                right += flagged and all(e in codes for e in c["expected"])
            else:
                fp, tn = fp + flagged, tn + (not flagged)
        entry = prf(tp, fp, fn)
        entry["false_alarm_rate"] = fp / (fp + tn) if fp + tn else None
        entry["recall_by_kind"] = {k: by_kind[k] / n for k, n in seen.items()}
        if system == "full":
            entry["right_code_rate"] = right / tp if tp else None
        result[system] = entry
    return result


def repair_rate(cases: list) -> dict:
    """Of the repairable corruptions, how many does the repair fix; how many does it break."""
    todo = [c for c in cases if c["positive"] and c["repairable"]]
    fixed = 0
    for c in todo:
        out = repair(compile_text(c["source"], "en"), c["target"])
        fixed += out.after.status == "pass" and out.repaired
    return {"repairable": len(todo), "fixed": fixed, "rate": fixed / len(todo) if todo else None}


def hand_cases(sentences: dict, split: str) -> list:
    cases = []
    for r in read_jsonl(ROOT / "data" / "corruptions.jsonl"):
        if sentences[r["source_id"]]["split"] != split:
            continue
        cases.append(
            {
                "id": r["id"],
                "source": r["source"],
                "target": r["target"],
                "positive": r["kind"] == "corruption",
                "expected": r["expected"],
                "repairable": r["repairable"],
                "tag": "hand",
            }
        )
    return cases


def mt_cases(sentences: dict, path: Path, split: str) -> tuple:
    """Corrupted copies of real MT output, plus the untouched outputs (not gold negatives)."""
    corrupted, clean = [], []
    for r in read_jsonl(path):
        s = sentences.get(r["id"])
        if not s or s["split"] != split:
            continue
        clean.append({"id": r["id"], "source": r["source"], "target": r["hindi"]})
        for kind, text, code in mutate(s, r["hindi"]):
            corrupted.append(
                {
                    "id": r["id"],
                    "source": r["source"],
                    "target": text,
                    "positive": True,
                    "expected": [code],
                    "repairable": kind != "negation",
                    "tag": kind,
                }
            )
    return corrupted, clean


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", default="dev", choices=["dev", "heldout"])
    ap.add_argument("--i-know-this-spends-heldout", action="store_true")
    ap.add_argument("--mt", help="translations.jsonl: corrupt the real MT output as well")
    ap.add_argument("--out", default=str(ROOT / "reports" / "verifier_eval.json"))
    args = ap.parse_args(argv)
    if args.split == "heldout" and not args.i_know_this_spends_heldout:
        print("held-out is used once, at the final evaluation: add --i-know-this-spends-heldout")
        return 2
    sentences = {r["id"]: r for r in read_jsonl(ROOT / "data" / "sentences.jsonl")}
    report = {"split": args.split}
    cases = hand_cases(sentences, args.split)
    report["hand"] = {"n": len(cases), "systems": score(cases), "repair": repair_rate(cases)}
    if args.mt:
        corrupted, clean = mt_cases(sentences, Path(args.mt), args.split)
        report["mt_corrupted"] = {
            "n": len(corrupted),
            "systems": score(corrupted),
            "repair": repair_rate(corrupted),
        }
        flagged = []
        for c in clean:
            hit, codes = flags("full", c["source"], c["target"])
            if hit:
                flagged.append({**c, "codes": codes})
        report["mt_clean"] = {"n": len(clean), "flagged": len(flagged)}
        report["flagged_mt"] = flagged
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    for name in ("hand", "mt_corrupted"):
        if name in report:
            print(f"{name}: n={report[name]['n']}  repair {report[name]['repair']}")
            for system, m in report[name]["systems"].items():
                f = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
                print(
                    f"  {system:7} P {f(m['precision'])} R {f(m['recall'])} F1 {f(m['f1'])} "
                    f"false-alarm {f(m['false_alarm_rate'])}"
                )
    if "mt_clean" in report:
        print(f"real MT output flagged by the verifier: {report['mt_clean']} (review by hand)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
