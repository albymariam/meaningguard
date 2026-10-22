"""The smoke script's own logic, with fake translators (the real run needs the models)."""

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from meaningguard.config import load_config
from meaningguard.pipeline import ChainTranslator, Pipeline
from meaningguard.translator import GoogleFallback

spec = importlib.util.spec_from_file_location(
    "smoke_pipeline", Path(__file__).resolve().parent.parent / "tools" / "smoke_pipeline.py"
)
smoke = importlib.util.module_from_spec(spec)
sys.modules["smoke_pipeline"] = smoke  # dataclasses look the module up here
spec.loader.exec_module(smoke)


class Echo:
    name = "echo"

    def translate(self, sentences):
        return ["₹५०० दें।" for _ in sentences]


def statuses(checks):
    return {c.name.split(" [")[0]: c.status for c in checks}


def fake_google(monkeypatch, answer="₹५०० शुक्रवार तक दें।"):
    class Client:
        def __init__(self, source, target):
            pass

        def translate(self, text):
            return answer

    monkeypatch.setitem(sys.modules, "deep_translator", SimpleNamespace(GoogleTranslator=Client))


def test_all_checks_pass_with_working_translators(monkeypatch):
    fake_google(monkeypatch)
    cfg = replace(load_config(), revisions={})  # independent of what config.yaml pins
    pipeline = Pipeline(ChainTranslator([Echo()], 5))
    checks, warm = smoke.run_checks(pipeline, cfg, skip_google=False)
    by = {c.name: c.status for c in checks}
    assert by["repair example (provided translation)"] == "pass"
    assert by["fallback to Google (forced)"] == "pass"
    assert by["model versions pinned"] == "warn"
    assert not [c for c in checks if c.status == "fail"], [c for c in checks if c.status == "fail"]
    assert len(warm) == len(smoke.SENTENCES) - 1


def test_a_broken_primary_is_a_failure_not_a_fallback(monkeypatch):
    fake_google(monkeypatch)
    pipeline = Pipeline(ChainTranslator([smoke.Broken()], 5))
    checks, _ = smoke.run_checks(pipeline, load_config(), skip_google=True)
    translate = [c for c in checks if c.name.startswith("translate+verify")]
    assert translate and all(c.status == "fail" for c in translate)
    assert [c.status for c in checks if c.name.startswith("fallback")] == ["skip"]


def test_a_dead_google_endpoint_fails_the_fallback_check(monkeypatch):
    class Dead:
        def __init__(self, source, target):
            pass

        def translate(self, text):
            raise ConnectionError("offline")

    monkeypatch.setitem(sys.modules, "deep_translator", SimpleNamespace(GoogleTranslator=Dead))
    monkeypatch.setattr(smoke, "GoogleFallback", lambda: GoogleFallback(first_wait=0, attempts=2))
    checks, _ = smoke.run_checks(Pipeline(ChainTranslator([Echo()], 5)), load_config(), False)
    assert statuses(checks)["fallback to Google (forced)"] == "fail"


def test_main_writes_the_report_and_sets_the_exit_code(monkeypatch, tmp_path):
    import json

    import meaningguard.pipeline as pipeline_module

    def fake_build(cfg, device=None, translator=None):
        return Pipeline(ChainTranslator([translator or Echo()], 5))

    monkeypatch.setattr(pipeline_module, "build_pipeline", fake_build)
    out = tmp_path / "report.json"
    assert smoke.main(["--skip-google", "--out", str(out)]) == 0
    report = json.loads(out.read_text("utf-8"))
    assert report[0]["name"].startswith("translate+verify") and report[-1]["name"] == "latency"

    monkeypatch.setattr(
        pipeline_module,
        "build_pipeline",
        lambda cfg, device=None: fake_build(cfg, translator=smoke.Broken()),
    )
    assert smoke.main(["--skip-google", "--out", str(out)]) == 1


def test_a_rate_limited_google_is_a_warning(monkeypatch):
    class Limited:
        def __init__(self, source, target):
            pass

        def translate(self, text):
            raise ConnectionError("TooManyRequests: You made too many requests")

    monkeypatch.setitem(sys.modules, "deep_translator", SimpleNamespace(GoogleTranslator=Limited))
    monkeypatch.setattr(smoke, "GoogleFallback", lambda: GoogleFallback(first_wait=0, attempts=2))
    checks, _ = smoke.run_checks(Pipeline(ChainTranslator([Echo()], 5)), load_config(), False)
    assert statuses(checks)["fallback to Google (forced)"] == "warn"


def test_pinned_models_pass_the_pin_check():
    cfg = load_config()
    pinned = replace(cfg, revisions={cfg.models.en_indic: "abc"})
    checks, _ = smoke.run_checks(Pipeline(ChainTranslator([Echo()], 5)), pinned, skip_google=True)
    assert statuses(checks)["model versions pinned"] == "pass"


def test_tone_checks_grade_the_models():
    from meaningguard.tone import ToneResult

    class Checker:
        def __init__(self, sentiment, notes=(), source_ok=True):
            self.sentiment, self.notes, self.source_ok = sentiment, list(notes), source_ok

        def check(self, source, target):
            rec = {"sentiment": self.sentiment}
            return ToneResult(rec if self.source_ok else None, rec, [], self.notes)

    # the first sentence is negative, the second positive
    assert [c.status for c in smoke.tone_checks(Checker("negative"))] == ["pass", "warn"]
    assert [c.status for c in smoke.tone_checks(Checker("negative", ["no back"]))] == [
        "warn",
        "warn",
    ]
    assert {c.status for c in smoke.tone_checks(Checker("x", ["down"], source_ok=False))} == {
        "fail"
    }
