"""Build the Hugging Face Space bundle and upload it (Docker Space, free CPU).

    python tools/deploy_space.py --repo <user>/<name> --dry-run   # only build ./space_build
    python tools/deploy_space.py --repo <user>/<name>             # build and upload

The token comes from HF_TOKEN or from a previous `huggingface-cli login`. The Space is public.
It refuses to deploy unpinned models unless you pass --allow-unpinned.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meaningguard.config import load_config  # noqa: E402
from tools.prefetch_models import model_ids  # noqa: E402

FILES = ["app.py", "requirements.txt", "config.yaml", ".streamlit/config.toml"]
TREES = ["meaningguard"]
TOOLS = ["prefetch_models.py"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")


def unpinned(cfg) -> list:
    return [m for m in model_ids(cfg) if m not in cfg.revisions]


def stage(dest: Path, root: Path = ROOT) -> list:
    """Copy what the Space needs into ``dest`` (emptied first); returns the relative paths."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for name in FILES:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
    for tree in TREES:
        shutil.copytree(root / tree, dest / tree, ignore=SKIP)
    (dest / "tools").mkdir()
    for name in TOOLS:
        shutil.copy2(root / "tools" / name, dest / "tools" / name)
    shutil.copy2(root / "deploy" / "Dockerfile", dest / "Dockerfile")
    shutil.copy2(root / "deploy" / "space_README.md", dest / "README.md")
    return sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())


def upload(dest: Path, repo: str, token: str | None) -> str:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    api.upload_folder(
        folder_path=str(dest),
        repo_id=repo,
        repo_type="space",
        commit_message="Deploy MeaningGuard",
    )
    return f"https://huggingface.co/spaces/{repo}"


def main(argv=None) -> int:
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="<user>/<space name>")
    parser.add_argument("--dry-run", action="store_true", help="build the bundle, do not upload")
    parser.add_argument("--allow-unpinned", action="store_true")
    parser.add_argument("--out", default=str(ROOT / "space_build"))
    args = parser.parse_args(argv)
    if not args.dry_run and not args.repo:
        parser.error("--repo is required unless --dry-run")

    missing = unpinned(load_config())
    if missing and not args.allow_unpinned:
        print("these models are not pinned in config.yaml:")
        for model in missing:
            print(f"  {model}")
        print("run: python tools/pin_models.py --write   (or pass --allow-unpinned)")
        return 1
    files = stage(Path(args.out))
    print(f"built {args.out} ({len(files)} files)")
    if args.dry_run:
        return 0
    print("uploaded:", upload(Path(args.out), args.repo, os.environ.get("HF_TOKEN")))
    print("The first build takes 10 to 20 minutes (it downloads the models).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
