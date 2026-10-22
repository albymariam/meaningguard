"""Compare the emotion models with human labels (dev only unless told otherwise).

    python tools/eval_emotion.py                      # needs your_emotion filled in the sheet
    python tools/eval_emotion.py --labels2 other.csv  # second annotator: kappa, agreed rows only
    python tools/eval_emotion.py --use-proposed       # smoke run on the unreviewed proposals
    python tools/eval_emotion.py --target             # also the Hindi side (needs --back data)

Thresholds in ``config.yaml`` (``emotion:``) are tuned on dev here; held-out is read once, at the
end (``--split heldout --i-know-this-spends-heldout``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meaningguard.emotion import EMOTION_SENTIMENT, EMOTIONS  # noqa: E402

SHEET = ROOT / "data" / "review" / "emotion_labels.csv"
YES = {"y", "yes", "true", "1"}
NO = {"n", "no", "false", "0"}


def read_jsonl(path: Path) -> list:
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def read_sheet(path: Path) -> dict:
    """id -> (emotion or None, sarcasm True/False/None) from one annotator's filled copy."""
    out = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            emotion = (row.get("your_emotion") or "").strip().lower() or None
            if emotion is not None and emotion not in EMOTIONS:
                raise ValueError(f"{path.name}: {row['id']}: unknown emotion {emotion!r}")
            mark = (row.get("your_sarcasm (Y/N)") or "").strip().lower()
            if mark and mark not in YES | NO:
                raise ValueError(f"{path.name}: {row['id']}: sarcasm must be Y or N")
            out[row["id"]] = (emotion, True if mark in YES else False if mark in NO else None)
    return out


def kappa(pairs: list) -> float | None:
    """Cohen's kappa of two annotators' labels."""
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    a_count, b_count = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    chance = sum(a_count[k] * b_count[k] for k in a_count) / (n * n)
    return None if chance == 1 else (observed - chance) / (1 - chance)


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else None
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "fp": fp, "fn": fn}


def gold_rows(sentences: list, sheets: list, use_proposed: bool, split: str) -> tuple:
    """Rows with a gold label, and a note on how the gold was made."""
    notes: list = []
    agreed: dict = {}
    if sheets:
        first = sheets[0]
        for key, (emotion, sarcasm) in first.items():
            mates = [s.get(key) for s in sheets[1:]]
            if all(m == (emotion, sarcasm) for m in mates):
                agreed[key] = (emotion, sarcasm)
        if len(sheets) > 1:
            pairs = [
                (a[0], b[0])
                for k, a in sheets[0].items()
                if (b := sheets[1].get(k)) and a[0] and b[0]
            ]
            k = kappa(pairs)
            notes.append(
                f"annotators agree on {len(agreed)} of {len(first)} rows; kappa(emotion) = "
                + (f"{k:.2f}" if k is not None else "n/a")
            )
    rows = []
    for s in sentences:
        if s["split"] != split or not s.get("emotion"):
            continue
        if sheets:
            emotion, sarcasm = agreed.get(s["id"], (None, None))
        elif use_proposed:
            emotion, sarcasm = s["emotion"], s.get("sarcasm")
        else:
            continue
        if emotion:
            rows.append({"id": s["id"], "text": s["text"], "emotion": emotion, "sarcasm": sarcasm})
    return rows, notes


def evaluate(rows: list, analyze_source, analyze_target=None, backs: dict | None = None) -> dict:
    """Score the analyzer against ``rows``. ``backs[id]`` is (hindi, english back-translation)."""
    confusion: Counter = Counter()
    label_ok = label_n = sent_ok = sent_n = uncertain = 0
    tp = fp = fn = 0
    per_signal: dict = {}
    target = Counter()
    mistakes = []
    irony: list = []
    for row in rows:
        rec = analyze_source(row["text"])
        gold_sent = EMOTION_SENTIMENT.get(row["emotion"])
        confusion[(row["emotion"], rec["label"])] += 1
        if rec["label"] != "uncertain":
            label_n += 1
            label_ok += rec["label"] == row["emotion"]
        if rec["sentiment"] == "uncertain":
            uncertain += 1
        if gold_sent:
            sent_n += 1
            sent_ok += rec["sentiment"] == gold_sent
            for sg in rec["signals"]:
                if sg["sentiment"]:
                    stat = per_signal.setdefault(sg["name"], [0, 0])
                    stat[0] += sg["sentiment"] == gold_sent
                    stat[1] += 1
            if rec["sentiment"] != gold_sent:
                mistakes.append((row["id"], row["text"][:60], gold_sent, rec["sentiment"]))
        if row["sarcasm"] is not None:
            score = next((g["prob"] for g in rec["signals"] if g["name"].startswith("irony")), None)
            irony.append(
                (row["id"], row["text"][:50], row["sarcasm"], score, rec["possible_sarcasm"])
            )
            tp += row["sarcasm"] and rec["possible_sarcasm"]
            fp += (not row["sarcasm"]) and rec["possible_sarcasm"]
            fn += row["sarcasm"] and not rec["possible_sarcasm"]
        pair = (backs or {}).get(row["id"])
        if analyze_target and pair and gold_sent:
            t = analyze_target(pair[0], pair[1])
            target["n"] += 1
            target["ok"] += t["sentiment"] == gold_sent
            target["uncertain"] += t["sentiment"] == "uncertain"
    return {
        "rows": len(rows),
        "label": {
            "accuracy_when_labelled": ratio(label_ok, label_n),
            "coverage": ratio(label_n, len(rows)),
        },
        "sentiment": {
            "accuracy": ratio(sent_ok, sent_n),
            "uncertain_rate": ratio(uncertain, len(rows)),
            "single_signal": {k: ratio(a, b) for k, (a, b) in sorted(per_signal.items())},
        },
        "sarcasm": prf(tp, fp, fn),
        "irony_scores": irony,
        "irony_sweep": sweep(irony),
        "target": {
            "rows": target["n"],
            "accuracy": ratio(target["ok"], target["n"]),
            "uncertain_rate": ratio(target["uncertain"], target["n"]),
        },
        "confusion": {f"{g}->{p}": n for (g, p), n in sorted(confusion.items())},
        "sentiment_mistakes": mistakes,
    }


def sweep(irony: list, cuts=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)) -> dict:
    """Sarcasm precision/recall if the irony score alone were cut at each value."""
    scored = [(gold, score) for _, _, gold, score, _ in irony if score is not None]
    out = {}
    for cut in cuts:
        tp = sum(g and sc >= cut for g, sc in scored)
        fp = sum((not g) and sc >= cut for g, sc in scored)
        fn = sum(g and sc < cut for g, sc in scored)
        out[str(cut)] = prf(tp, fp, fn)
    return out


def ratio(a, b):
    return round(a / b, 3) if b else None


def ratio_or_na(value) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def show(result: dict) -> None:
    print(f"rows: {result['rows']}")
    lab, sen, sar, tgt = result["label"], result["sentiment"], result["sarcasm"], result["target"]
    print(f"emotion label: accuracy {lab['accuracy_when_labelled']} (coverage {lab['coverage']})")
    print(f"sentiment (vote): accuracy {sen['accuracy']}, uncertain {sen['uncertain_rate']}")
    for name, acc in sen["single_signal"].items():
        print(f"  {name}: {acc}")
    print(
        f"sarcasm: precision {sar['precision']}, recall {sar['recall']}, f1 {sar['f1']} "
        f"(tp {sar['tp']}, fp {sar['fp']}, fn {sar['fn']})"
    )
    print("irony score by row (gold sarcasm / predicted):")
    for rid, text, gold, score, pred in sorted(result["irony_scores"], key=lambda r: -(r[3] or 0)):
        shown = "n/a" if score is None else f"{score:.2f}"
        print(f"  {shown} gold={'Y' if gold else 'N'} pred={'Y' if pred else 'N'} {rid} {text}")
    print("cut on the irony score alone:")
    for cut, m in result["irony_sweep"].items():
        prec, rec = ratio_or_na(m["precision"]), ratio_or_na(m["recall"])
        print(f"  >= {cut}: precision {prec}, recall {rec}")
    if tgt["rows"]:
        print(
            f"translation sentiment: accuracy {tgt['accuracy']}, "
            f"uncertain {tgt['uncertain_rate']} ({tgt['rows']} rows)"
        )
    print("confusion (gold->predicted):")
    for key, n in result["confusion"].items():
        print(f"  {key}: {n}")
    for row in result["sentiment_mistakes"]:
        print("  wrong:", *row)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(SHEET), help="your filled copy of the label sheet")
    ap.add_argument("--labels2", help="a second annotator's filled copy")
    ap.add_argument(
        "--use-proposed", action="store_true", help="score against the unreviewed proposals"
    )
    ap.add_argument("--split", default="dev", choices=["dev", "heldout"])
    ap.add_argument("--i-know-this-spends-heldout", action="store_true")
    ap.add_argument("--target", action="store_true", help="also score the Hindi side")
    ap.add_argument("--translations", default=str(ROOT / "data" / "translations.jsonl"))
    ap.add_argument("--device", default=None)
    ap.add_argument("--out", default=str(ROOT / "reports" / "emotion_eval.json"))
    args = ap.parse_args(argv)
    if args.split == "heldout" and not args.i_know_this_spends_heldout:
        print("held-out is used once, at the final evaluation: add --i-know-this-spends-heldout")
        return 2

    sheets = [read_sheet(Path(p)) for p in (args.labels, args.labels2) if p]
    if not any(emotion for sheet in sheets for emotion, _ in sheet.values()):
        sheets = []
    sentences = read_jsonl(ROOT / "data" / "sentences.jsonl")
    rows, notes = gold_rows(sentences, sheets, args.use_proposed, args.split)
    if not rows:
        print("no gold labels yet: fill your_emotion in the sheet (or try --use-proposed)")
        return 1
    if args.use_proposed and not sheets:
        notes.append("gold = the author's unreviewed proposals; numbers are indicative only")

    from meaningguard.emotion import build_analyzer

    analyzer = build_analyzer(device=args.device)
    backs = None
    if args.target:
        path = Path(args.translations)
        if not path.exists():
            print(f"{path.name} not found: run tools/translate_dataset.py --split dev --back")
            return 1
        backs = {
            r["id"]: (r["hindi"], r["back_translation"])
            for r in read_jsonl(path)
            if r.get("back_translation")
        }
    result = evaluate(rows, analyzer.analyze_source, analyzer.analyze_target, backs)
    result["notes"], result["split"] = notes, args.split
    show(result)
    for note in notes:
        print("note:", note)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
