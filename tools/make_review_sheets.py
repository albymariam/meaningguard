"""Write the two CSV sheets a reviewer fills in (open them in Excel or Google Sheets).

data/review/hindi_review.csv     Hindi reference and corrupted targets to confirm
data/review/emotion_labels.csv   emotion and sarcasm labels; each annotator fills a copy
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "review"


def rows(name: str) -> list[dict]:
    path = ROOT / "data" / name
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def wrong(pair: dict) -> str:
    what = "; ".join(pair["expected"]) or "nothing (control: an equivalent translation)"
    return f"{what} - {pair['note']}" if pair["note"] else what


def write(path: Path, header: list[str], body: list[list]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:  # BOM so Excel reads Hindi
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(body)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pairs = rows("corruptions.jsonl")
    write(
        OUT / "hindi_review.csv",
        [
            "id",
            "kind",
            "english",
            "hindi_reference",
            "hindi_target",
            "what_is_wrong_in_target",
            "reference_ok (Y/N)",
            "target_wrong_as_described (Y/N)",
            "corrected_hindi",
            "notes",
        ],
        [
            [p["id"], p["kind"], p["source"], p["reference"], p["target"], wrong(p), "", "", "", ""]
            for p in pairs
        ],
    )
    labelled = [
        r for r in rows("sentences.jsonl") if r["category"] in ("emotion", "sarcasm", "plain")
    ]
    header = [
        "id",
        "sentence",
        "proposed_emotion",
        "proposed_sarcasm",
        "your_emotion",
        "your_sarcasm (Y/N)",
        "notes",
    ]

    def body(items):
        return [
            [
                r["id"],
                r["text"],
                r["emotion"],
                {True: "Y", False: "N", None: ""}[r["sarcasm"]],
                "",
                "",
                "",
            ]
            for r in items
        ]

    # round 1 (s153-s194) is already labelled by hand: its file must never be regenerated over
    first = [r for r in labelled if int(r["id"][1:]) <= 194]
    second = [r for r in labelled if int(r["id"][1:]) > 194]
    write(OUT / "emotion_labels.csv", header, body(first))
    write(OUT / "emotion_labels_2.csv", header, body(second))
    print(f"wrote {OUT / 'hindi_review.csv'} ({len(pairs)} rows)")
    print(f"wrote emotion_labels.csv ({len(first)}) and emotion_labels_2.csv ({len(second)})")


if __name__ == "__main__":
    main()
