"""Compare the IR of each English sentence with the IR of its IndicTrans2 Hindi translation.

Dev only by default, so Hindi grammar rules are tuned on dev and held-out stays unseen.

    python tools/compare_translations.py                 # dev, prints only differences
    python tools/compare_translations.py --all-rows      # also prints matches
    python tools/compare_translations.py --split heldout --i-know-this-spends-heldout
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meaningguard.ir import compile_text  # noqa: E402


def load(path):
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def short(fact):
    parts = [fact["kind"]]
    for k in ("direction", "op", "rel", "scope", "lit", "text", "role"):
        if k in fact:
            parts.append(str(fact[k]))
    for k in ("value", "from", "to", "delta", "low", "high", "anchor"):
        v = fact.get(k)
        if isinstance(v, dict):
            parts.append(
                f"{k}={v.get('type')}:" + ",".join(str(x) for a, x in v.items() if a != "type")
            )
    t = fact.get("target")
    if isinstance(t, dict) and "kind" in t:
        parts.append("[" + short(t) + "]")
    return " ".join(parts)


def signature(ir):
    return sorted(
        (c["polarity"], (c["cond"] or {}).get("op"), (c["cond"] or {}).get("role"), short(f))
        for c in ir.clauses
        for f in c["facts"]
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "heldout", "all"])
    ap.add_argument("--i-know-this-spends-heldout", action="store_true")
    ap.add_argument("--all-rows", action="store_true")
    args = ap.parse_args()
    if args.split != "dev" and not args.i_know_this_spends_heldout:
        sys.exit("Refusing: held-out is used once, in Phase 9. Pass --i-know-this-spends-heldout.")

    data = ROOT / "data"
    sentences = {r["id"]: r for r in load(data / "sentences.jsonl")}
    translations = load(data / "translations.jsonl")
    match = diff = 0
    for t in translations:
        row = sentences[t["id"]]
        if args.split != "all" and row["split"] != args.split:
            continue
        hi_text = t["hindi"]
        en, hi = compile_text(row["text"], "en"), compile_text(hi_text, "hi")
        ok = en.fp == hi.fp
        match += ok
        diff += not ok
        if ok and not args.all_rows:
            continue
        print(f"{'OK  ' if ok else 'DIFF'} {t['id']} tier {en.tier}/{hi.tier}")
        print("  EN:", row["text"])
        print("  HI:", hi_text)
        if not ok:
            a, b = signature(en), signature(hi)
            for x in a:
                if x not in b:
                    print("   - only EN:", x)
            for x in b:
                if x not in a:
                    print("   + only HI:", x)
            print("   codes HI:", sorted(c for c in hi.codes() if c != "NON_QUANTITY"))
    print(f"\n{match} match, {diff} differ ({args.split})")


if __name__ == "__main__":
    main()
