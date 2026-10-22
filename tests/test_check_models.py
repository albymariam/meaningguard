import json

from meaningguard import check_models as cm


class GatedRepoError(Exception):
    pass


class RepositoryNotFoundError(Exception):
    pass


def test_python_check_passes_on_supported_interpreter():
    assert cm.check_python().status == cm.PASS


def test_platform_check_never_fails():
    assert cm.check_platform().status in {cm.PASS, cm.WARN}


def test_explain_gated_error_mentions_token_and_url():
    message = cm.explain_hf_error("ai4bharat/x", GatedRepoError("denied"))
    assert "HF_TOKEN" in message
    assert "https://huggingface.co/ai4bharat/x" in message


def test_explain_not_found_error():
    assert "not found" in cm.explain_hf_error("a/b", RepositoryNotFoundError("404"))


def test_explain_unknown_error_keeps_type():
    assert cm.explain_hf_error("a/b", ValueError("boom")).startswith("ValueError")


def test_timed_converts_exceptions_to_failures():
    def broken():
        raise RuntimeError("bad")

    result = cm._timed("x", broken)
    assert result.status == cm.FAIL
    assert "RuntimeError" in result.detail


def _fake_hub(monkeypatch, checked, fail_on=None):
    import sys
    import types

    def hf_hub_url(model_id, filename):
        return f"{model_id}/{filename}"

    def get_hf_file_metadata(url, token=None):
        checked.append((url, token))
        if fail_on and fail_on in url:
            raise GatedRepoError("401")

    module = types.SimpleNamespace(hf_hub_url=hf_hub_url, get_hf_file_metadata=get_hf_file_metadata)
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)


def test_access_check_probes_remote_code_file_for_indictrans(monkeypatch):
    checked = []
    _fake_hub(monkeypatch, checked)
    result = cm.check_model_access("ai4bharat/indictrans2-indic-en-dist-200M", "tok")
    assert result.status == cm.PASS
    assert [u.split("/")[-1] for u, _ in checked] == ["config.json", "modeling_indictrans.py"]
    assert all(t == "tok" for _, t in checked)


def test_access_check_probes_only_config_for_other_models(monkeypatch):
    checked = []
    _fake_hub(monkeypatch, checked)
    assert cm.check_model_access("cardiffnlp/x", None).status == cm.PASS
    assert [u.split("/")[-1] for u, _ in checked] == ["config.json"]


def test_access_check_fails_when_code_file_is_gated(monkeypatch):
    _fake_hub(monkeypatch, [], fail_on="modeling_indictrans.py")
    result = cm.check_model_access("ai4bharat/indictrans2-indic-en-dist-200M", "tok")
    assert result.status == cm.FAIL
    assert "Read token" in result.detail


def test_failed_check_reports_where_it_failed():
    def inner():
        raise AttributeError("'NoneType' object has no attribute 'shape'")

    def broken():
        inner()

    result = cm._timed("x", broken)
    assert result.status == cm.FAIL
    assert "AttributeError" in result.detail
    assert "test_check_models.py" in result.detail and "inner" in result.detail


def test_google_check_retries_then_fails_with_last_error(monkeypatch):
    import sys
    import types

    calls = []

    class Fake:
        def __init__(self, **kwargs):
            pass

        def translate(self, text):
            calls.append(1)
            raise RuntimeError("too many requests")

    fake_module = types.SimpleNamespace(GoogleTranslator=Fake)
    monkeypatch.setitem(sys.modules, "deep_translator", fake_module)
    monkeypatch.setattr(cm.time, "sleep", lambda s: None)
    result = cm.check_google_fallback(attempts=3, first_wait=0)
    assert result.status == cm.FAIL and len(calls) == 3
    assert "too many requests" in result.detail


def test_google_check_succeeds_after_a_retry(monkeypatch):
    import sys
    import types

    state = {"n": 0}

    class Fake:
        def __init__(self, **kwargs):
            pass

        def translate(self, text):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("rate limited")
            return "कीमत गिरी"

    fake_module = types.SimpleNamespace(GoogleTranslator=Fake)
    monkeypatch.setitem(sys.modules, "deep_translator", fake_module)
    monkeypatch.setattr(cm.time, "sleep", lambda s: None)
    result = cm.check_google_fallback(attempts=3, first_wait=0)
    assert result.status == cm.PASS and "attempt 2" in result.detail


def _fake_whoami(monkeypatch, result=None, error=None):
    import sys
    import types

    def whoami(token=None):
        if error is not None:
            raise error
        return result

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(whoami=whoami))


class _Http401(Exception):
    def __init__(self):
        super().__init__("401")
        self.response = type("R", (), {"status_code": 401})()


def test_missing_token_is_a_failure():
    assert cm.check_hf_token(None).status == cm.FAIL


def test_valid_read_token_passes_and_reports_account(monkeypatch):
    _fake_whoami(monkeypatch, {"name": "alby", "auth": {"accessToken": {"role": "read"}}})
    result = cm.check_hf_token("hf_x")
    assert result.status == cm.PASS and "account=alby" in result.detail


def test_fine_grained_token_is_a_warning(monkeypatch):
    _fake_whoami(monkeypatch, {"name": "a", "auth": {"accessToken": {"role": "fineGrained"}}})
    assert cm.check_hf_token("hf_x").status == cm.WARN


def test_rejected_token_is_a_failure(monkeypatch):
    _fake_whoami(monkeypatch, error=_Http401())
    result = cm.check_hf_token("hf_yourtokenhere")
    assert result.status == cm.FAIL and "rejected" in result.detail


def test_unverifiable_token_is_a_warning_not_a_failure(monkeypatch):
    _fake_whoami(monkeypatch, error=ConnectionError("dns"))
    assert cm.check_hf_token("hf_x").status == cm.WARN


def test_resolve_device_respects_explicit_choice():
    assert cm.resolve_device("cpu") == "cpu"
    assert cm.resolve_device("cuda") == "cuda"


def test_missing_toolkit_is_warning_on_windows_failure_elsewhere(monkeypatch):
    real_import = cm.importlib.import_module

    def fake_import(name):
        if name == "IndicTransToolkit":
            raise ModuleNotFoundError(name)
        return real_import(name)

    monkeypatch.setattr(cm.importlib, "import_module", fake_import)

    def status_for(system):
        monkeypatch.setattr(cm.platform, "system", lambda: system)
        checks, _ = cm.check_modules()
        return next(c.status for c in checks if c.name == "package:IndicTransToolkit")

    assert status_for("Windows") == cm.WARN
    assert status_for("Linux") == cm.FAIL


def test_report_is_json_serialisable():
    checks = [cm.Check("a", cm.PASS, "ok", 0.1), cm.Check("b", cm.FAIL, "भाषा", 0.2)]
    report = cm.build_report(checks, {"torch": "2.5.0"}, "cpu")
    assert json.loads(json.dumps(report, ensure_ascii=False))["checks"][1]["status"] == cm.FAIL


def test_main_returns_2_on_bad_config(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("models: 1\n", encoding="utf-8")
    assert cm.main(["--config", str(bad), "--report", str(tmp_path / "r.json")]) == 2
    assert "config error" in capsys.readouterr().err
