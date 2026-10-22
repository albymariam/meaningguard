"""Pin every model of config.yaml to the commit it has today, so results stay reproducible.

    python tools/pin_models.py            # print the revisions block
    python tools/pin_models.py --write    # put it into config.yaml

Needs internet (and the Hugging Face login for the gated IndicTrans2 models).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meaningguard.config import DEFAULT_PATH, load_config  # noqa: E402


def model_ids(cfg) -> list:
    ids = [cfg.models.en_indic, cfg.models.indic_en, cfg.models.sentiment]
    ids += [cfg.models.emotion_en, cfg.models.irony_en]
    return list(dict.fromkeys(ids))


def fetch_revisions(ids: list, api) -> dict:
    return {model: api.model_info(model).sha for model in ids}


def render_block(revisions: dict) -> str:
    lines = ["revisions:"] + [f"  {model}: {sha}" for model, sha in sorted(revisions.items())]
    return "\n".join(lines) + "\n"


def replace_block(text: str, block: str) -> str:
    """Replace the top-level ``revisions:`` block (and its comment line) or append a new one."""
    pattern = re.compile(r"(?m)^revisions:.*(?:\n[ \t]+.*)*\n?")
    if pattern.search(text):
        return pattern.sub(lambda _: block, text, count=1)
    return text.rstrip("\n") + "\n\n" + block


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    from huggingface_hub import HfApi

    revisions = fetch_revisions(model_ids(load_config()), HfApi())
    block = render_block(revisions)
    print(block)
    if args.write:
        DEFAULT_PATH.write_text(
            replace_block(DEFAULT_PATH.read_text(encoding="utf-8"), block), encoding="utf-8"
        )
        print(f"written to {DEFAULT_PATH}")


if __name__ == "__main__":
    main()
