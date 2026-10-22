import pytest

from meaningguard.config import ConfigError, load_config

VALID = """
models:
  en_indic: a/en-indic
  indic_en: a/indic-en
  sentiment: a/sentiment
  emotion_en: a/emotion
  irony_en: a/irony
runtime:
  device: cpu
  num_beams: 5
  max_length: 256
languages:
  source: eng_Latn
  target: hin_Deva
"""


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_default_config_loads():
    cfg = load_config()
    assert cfg.models.en_indic.startswith("ai4bharat/")
    assert cfg.runtime.num_beams >= 1
    assert cfg.languages.source != cfg.languages.target


def test_valid_config(tmp_path):
    cfg = load_config(write(tmp_path, VALID))
    assert cfg.runtime.device == "cpu"
    assert cfg.models.irony_en == "a/irony"


def test_use_cache_defaults_true_and_can_be_disabled(tmp_path):
    assert load_config(write(tmp_path, VALID)).runtime.use_cache is True
    off = VALID.replace("max_length: 256", "max_length: 256\n  use_cache: false")
    assert load_config(write(tmp_path, off)).runtime.use_cache is False


def test_use_cache_must_be_boolean(tmp_path):
    bad = VALID.replace("max_length: 256", "max_length: 256\n  use_cache: maybe")
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, bad))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.yaml")


def test_invalid_yaml(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "models: [unclosed"))


def test_non_mapping(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "- just\n- a list\n"))


def test_missing_section(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, VALID.replace("languages:", "other:")))


@pytest.mark.parametrize(
    "old,new",
    [
        ("device: cpu", "device: tpu"),
        ("num_beams: 5", "num_beams: 0"),
        ("num_beams: 5", "num_beams: true"),
        ("max_length: 256", "max_length: many"),
        ("en_indic: a/en-indic", "en_indic: ''"),
    ],
)
def test_invalid_values(tmp_path, old, new):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, VALID.replace(old, new)))
