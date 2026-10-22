import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from meaningguard.config import ConfigError, load_config

spec = importlib.util.spec_from_file_location(
    "pin_models", Path(__file__).resolve().parent.parent / "tools" / "pin_models.py"
)
pin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pin)

CONFIG = "models:\n  a: b\n\n# comment\nrevisions: {}\n"


def test_fetch_and_render():
    api = SimpleNamespace(model_info=lambda m: SimpleNamespace(sha="sha-" + m))
    block = pin.render_block(pin.fetch_revisions(["x/b", "x/a"], api))
    assert block == "revisions:\n  x/a: sha-x/a\n  x/b: sha-x/b\n"


def test_model_ids_are_unique_and_complete():
    ids = pin.model_ids(load_config())
    assert len(ids) == len(set(ids)) == 5


def test_replace_existing_flow_style_block():
    out = pin.replace_block(CONFIG, "revisions:\n  m: s1\n")
    assert out == "models:\n  a: b\n\n# comment\nrevisions:\n  m: s1\n"


def test_replace_block_style_block_and_keep_what_follows():
    text = "revisions:\n  old: 1\n  older: 2\nother: x\n"
    assert pin.replace_block(text, "revisions:\n  m: s\n") == "revisions:\n  m: s\nother: x\n"


def test_append_when_absent():
    assert pin.replace_block("models:\n  a: b\n", "revisions:\n  m: s\n").endswith(
        "\n\nrevisions:\n  m: s\n"
    )


def test_written_config_loads_back(tmp_path):
    text = (Path(__file__).resolve().parent.parent / "config.yaml").read_text("utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        pin.replace_block(text, "revisions:\n  ai4bharat/x: deadbeef\n"), encoding="utf-8"
    )
    assert load_config(path).revisions == {"ai4bharat/x": "deadbeef"}


def test_bad_revisions_are_rejected(tmp_path):
    text = (Path(__file__).resolve().parent.parent / "config.yaml").read_text("utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(pin.replace_block(text, "revisions:\n  x: ''\n"), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
