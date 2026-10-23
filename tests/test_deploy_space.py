"""Space bundle, model prefetch and the deploy tool (no network, no upload)."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from meaningguard.config import load_config

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


prefetch = load("prefetch_models")
deploy = load("deploy_space")


def pinned_cfg():
    cfg = load_config()
    return replace(cfg, revisions={m: f"rev{i}" for i, m in enumerate(prefetch.model_ids(cfg))})


def test_model_ids_and_skip_tone():
    cfg = load_config()
    assert len(prefetch.model_ids(cfg)) == 5 and len(prefetch.model_ids(cfg, skip_tone=True)) == 2
    assert prefetch.model_ids(cfg)[:2] == [cfg.models.en_indic, cfg.models.indic_en]


def test_prefetch_downloads_each_model_at_its_pin(capsys):
    calls = []
    cfg = pinned_cfg()
    done = prefetch.prefetch(cfg, lambda m, **kw: calls.append((m, kw)))
    assert [m for m, _ in calls] == prefetch.model_ids(cfg)
    assert calls[0][1]["revision"] == "rev0" and "*.h5" in calls[0][1]["ignore_patterns"]
    assert done[1] == (cfg.models.indic_en, "rev1")
    unpinned = prefetch.prefetch(replace(cfg, revisions={}), lambda m, **kw: None, skip_tone=True)
    assert unpinned == [(cfg.models.en_indic, None), (cfg.models.indic_en, None)]
    assert "not pinned" in capsys.readouterr().out


def test_stage_builds_exactly_the_space(tmp_path):
    files = deploy.stage(tmp_path / "out", ROOT)
    for needed in (
        "Dockerfile",
        "README.md",
        "app.py",
        "config.yaml",
        "requirements.txt",
        ".streamlit/config.toml",
        "meaningguard/theme.css",
        "meaningguard/pipeline.py",
        "tools/prefetch_models.py",
    ):
        assert needed in files
    assert not any("__pycache__" in f or f.startswith(("tests", "data", "docs")) for f in files)
    assert "tools/deploy_space.py" not in files


def test_stage_replaces_an_old_bundle(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("x")
    assert "stale.txt" not in deploy.stage(out, ROOT)


def test_space_readme_front_matter():
    text = (ROOT / "deploy" / "space_README.md").read_text("utf-8")
    head = text.split("---")[1]
    assert "sdk: docker" in head and "app_port: 8501" in head and "title: MeaningGuard" in head
    assert text.startswith("---\n")


def test_dockerfile_installs_prefetches_and_serves_on_8501():
    text = (ROOT / "deploy" / "Dockerfile").read_text("utf-8")
    assert "requirements.txt" in text and "tools/prefetch_models.py" in text
    assert "MEANINGGUARD_TONE=0" in text and "--server.port=8501" in text
    assert text.index("pip install") < text.index("prefetch_models.py") < text.index("CMD")
    assert "USER user" in text  # Spaces run as uid 1000, not root


def test_unpinned_detection():
    assert deploy.unpinned(pinned_cfg()) == []
    assert len(deploy.unpinned(replace(load_config(), revisions={}))) == 5


def test_main_refuses_unpinned_models(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(deploy, "load_config", lambda: replace(load_config(), revisions={}))
    assert deploy.main(["--dry-run", "--out", str(tmp_path / "o")]) == 1
    assert "pin_models.py --write" in capsys.readouterr().out
    assert deploy.main(["--dry-run", "--allow-unpinned", "--out", str(tmp_path / "o")]) == 0


def test_main_needs_a_repo_to_upload():
    with pytest.raises(SystemExit):
        deploy.main([])


def test_upload_creates_a_public_docker_space_then_uploads(monkeypatch, tmp_path):
    seen = {}

    class Api:
        def __init__(self, token=None):
            seen["token"] = token

        def create_repo(self, repo, **kw):
            seen["create"] = (repo, kw)

        def upload_folder(self, **kw):
            seen["upload"] = kw

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=Api))
    url = deploy.upload(tmp_path, "me/space", "tok")
    assert url == "https://huggingface.co/spaces/me/space" and seen["token"] == "tok"
    assert seen["create"][1] == {
        "repo_type": "space",
        "space_sdk": "docker",
        "private": False,
        "exist_ok": True,
    }
    assert seen["upload"]["repo_id"] == "me/space" and seen["upload"]["repo_type"] == "space"
