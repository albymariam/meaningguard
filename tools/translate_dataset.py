"""Translate data/sentences.jsonl with IndicTrans2 and save data/translations.jsonl.

Run on a machine with the models (see README). Resumable: ids already in the output are skipped.

    python tools/translate_dataset.py --split dev
    python tools/translate_dataset.py --split all --back
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meaningguard.check_models import resolve_device  # noqa: E402
from meaningguard.config import load_config  # noqa: E402
from meaningguard.translator import Translator  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--input", default=str(ROOT / "data" / "sentences.jsonl"))
    parser.add_argument("--output", default=str(ROOT / "data" / "translations.jsonl"))
    parser.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--limit", type=int, default=0, help="translate at most N new sentences")
    parser.add_argument("--back", action="store_true", help="also translate Hindi back to English")
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    out_path = Path(args.output)
    done = {row["id"] for row in read_jsonl(out_path)}
    todo = [
        row
        for row in read_jsonl(Path(args.input))
        if row["id"] not in done and args.split in ("all", row["split"])
    ]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("nothing to translate")
        return 0

    device = resolve_device(cfg.runtime.device)
    forward = Translator(
        cfg, cfg.models.en_indic, cfg.languages.source, cfg.languages.target, device
    )
    backward = Translator(
        cfg, cfg.models.indic_en, cfg.languages.target, cfg.languages.source, device
    )
    step = forward.batch_size
    with out_path.open("a", encoding="utf-8") as handle:
        for start in range(0, len(todo), step):
            chunk = todo[start : start + step]
            hindi = forward.translate([row["text"] for row in chunk])
            back = backward.translate(hindi) if args.back else [None] * len(chunk)
            for row, hi, en in zip(chunk, hindi, back, strict=True):
                record = {
                    "id": row["id"],
                    "source": row["text"],
                    "hindi": hi,
                    "model": cfg.models.en_indic,
                }
                if en is not None:
                    record["back_translation"] = en
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"{min(start + step, len(todo))}/{len(todo)} translated", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
