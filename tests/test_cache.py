"""The model-output cache: store, wrappers, wiring, command line (no models)."""

import json
import sqlite3
import threading
from dataclasses import replace

import pytest

from meaningguard import cache as cache_module
from meaningguard.cache import (
    Cache,
    CachedClassifier,
    CachedTranslator,
    Tally,
    make_key,
    open_cache,
)
from meaningguard.config import CacheSettings, ConfigError, load_config
from meaningguard.emotion import EmotionAnalyzer, HfClassifier
from meaningguard.pipeline import ChainTranslator, Pipeline, build_pipeline
from meaningguard.translator import GoogleFallback, Translator
from tests.test_config import VALID, write

# -- the store ---------------------------------------------------------------------------------


def test_get_set_and_counters():
    c = Cache()
    assert c.get("t", ["a"]) is None and (c.hits, c.misses) == (0, 1)
    c.set("t", ["a"], "कीमत")
    assert c.get("t", ["a"]) == "कीमत" and (c.hits, c.misses) == (1, 1)
    assert c.get("t", ["b"]) is None and c.get("other", ["a"]) is None
    c.set("t", ["a"], "new")  # replaces
    assert c.get("t", ["a"]) == "new"


def test_keys_are_stable_and_distinct():
    assert make_key(["a", 1, None]) == make_key(("a", 1, None))
    assert make_key(["a", "b"]) != make_key(["ab"]) and make_key(["a", 1]) != make_key(["a", "1"])
    assert len(make_key(["x"])) == 64


def test_stats_and_clear():
    c = Cache()
    c.set("translate", ["a"], "x")
    c.set("translate", ["b"], "y")
    c.set("classify", ["a"], {"joy": 0.5})
    stats = c.stats()
    assert stats["entries"] == {"classify": 1, "translate": 2} and stats["total"] == 3
    assert c.clear("classify") == 1 and c.stats()["total"] == 2
    assert c.clear() == 2 and c.stats()["total"] == 0


def test_survives_a_restart(tmp_path):
    path = tmp_path / "deep" / "dir" / "c.sqlite"
    first = Cache(path)
    first.set("t", ["x"], {"a": 1})
    first.close()
    assert Cache(path).get("t", ["x"]) == {"a": 1}
    assert path.exists() and Cache(path).stats()["bytes"] > 0


def test_a_broken_database_is_a_miss_not_an_error():
    c = Cache()
    c._db.execute("INSERT INTO kv VALUES ('t', ?, 'not json{')", (make_key(["bad"]),))
    assert c.get("t", ["bad"]) is None and c.errors == 1
    c.close()
    assert c.get("t", ["x"]) is None
    c.set("t", ["x"], "y")  # does not raise either
    assert c.errors >= 3


def test_unserialisable_values_are_ignored():
    c = Cache()
    c.set("t", ["x"], {1, 2})  # a set is not JSON
    assert c.get("t", ["x"]) is None and c.errors == 1


def test_threads_share_one_cache():
    c = Cache()

    def work(n):
        for i in range(50):
            c.set("t", [n, i], i)
            assert c.get("t", [n, i]) == i

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert c.stats()["total"] == 300 and c.errors == 0


# -- wrappers ----------------------------------------------------------------------------------


class Model:
    name = "fake"

    def __init__(self, key="v1", answer=None):
        self.cache_key, self.answer, self.calls, self.closed = (key,), answer, [], False

    def translate(self, sentences):
        self.calls.append(list(sentences))
        return [(self.answer or "हि ") + s for s in sentences]

    def classify(self, text):
        self.calls.append(text)
        return {"joy": 0.9}

    def close(self):
        self.closed = True


def test_only_new_sentences_reach_the_translator_in_order():
    inner, c = Model(), Cache()
    t = CachedTranslator(inner, c)
    assert t.translate(["a", "b"]) == ["हि a", "हि b"]
    assert t.translate(["b", "c", "a", "c"]) == ["हि b", "हि c", "हि a", "हि c"]
    assert inner.calls == [["a", "b"], ["c"]]  # c once even though it appears twice
    assert t.translate(["a"]) == ["हि a"] and len(inner.calls) == 2
    assert t.translate([]) == [] and t.name == "fake"


class Fixed(Model):
    """Answers every sentence with the same Hindi text."""

    def translate(self, sentences):
        self.calls.append(list(sentences))
        return [self.answer for _ in sentences]


def test_a_different_model_key_is_a_different_entry():
    c = Cache()
    a, b = Model("v1"), Model("v2", answer="X ")
    assert CachedTranslator(a, c).translate(["s"]) == ["हि s"]
    assert CachedTranslator(b, c).translate(["s"]) == ["X s"]


def test_blank_and_miscounted_answers_are_not_trusted():
    class Blank(Model):
        def translate(self, sentences):
            return ["" for _ in sentences]

    c = Cache()
    assert CachedTranslator(Blank(), c).translate(["a"]) == [""]
    assert c.stats()["total"] == 0  # not stored

    class Short(Model):
        def translate(self, sentences):
            return []

    with pytest.raises(RuntimeError, match="different number"):
        CachedTranslator(Short(), c).translate(["a"])


def test_translator_errors_pass_through_and_are_not_cached():
    class Down(Model):
        def translate(self, sentences):
            raise ConnectionError("offline")

    c = Cache()
    with pytest.raises(ConnectionError):
        CachedTranslator(Down(), c).translate(["a"])
    assert c.stats()["total"] == 0


def test_classifier_cache_and_close_delegation():
    inner, c = Model(), Cache()
    clf = CachedClassifier(inner, c)
    assert clf.classify("x") == {"joy": 0.9} and clf.classify("x") == {"joy": 0.9}
    assert inner.calls == ["x"]
    clf.close()
    assert inner.closed


def test_cache_keys_of_the_real_models():
    cfg = load_config()
    t = Translator(cfg, "m", "eng_Latn", "hin_Deva", revision="r")
    assert t.cache_key == (
        "m",
        "r",
        "eng_Latn",
        "hin_Deva",
        cfg.runtime.num_beams,
        cfg.runtime.max_length,
    )
    slower = replace(cfg, runtime=replace(cfg.runtime, num_beams=1))
    assert Translator(slower, "m", "eng_Latn", "hin_Deva", revision="r").cache_key != t.cache_key
    assert (
        Translator(cfg, "m", "eng_Latn", "hin_Deva", "cuda", revision="r").cache_key == t.cache_key
    )
    assert GoogleFallback().cache_key == ("google", "en", "hi")
    assert HfClassifier("m", "emotion", "r", max_length=64).cache_key == ("m", "r", "emotion", 64)


# -- setup -------------------------------------------------------------------------------------


def test_open_cache_respects_config_and_environment(monkeypatch, tmp_path):
    cfg = replace(load_config(), cache=CacheSettings(True, str(tmp_path / "c.sqlite")))
    assert open_cache(cfg) is None  # the test fixture sets MEANINGGUARD_CACHE=off
    monkeypatch.delenv("MEANINGGUARD_CACHE")
    assert isinstance(open_cache(cfg), Cache) and (tmp_path / "c.sqlite").exists()
    assert open_cache(replace(cfg, cache=CacheSettings(False, cfg.cache.path))) is None
    monkeypatch.setenv("MEANINGGUARD_CACHE", "False")
    assert open_cache(cfg) is None


def test_relative_paths_start_at_the_project_folder():
    path = cache_module.cache_path(load_config())
    assert path.is_absolute() and path.parent.name == ".cache"


def test_an_unusable_path_turns_the_cache_off(monkeypatch, tmp_path):
    monkeypatch.delenv("MEANINGGUARD_CACHE")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cfg = replace(load_config(), cache=CacheSettings(True, str(blocker / "sub" / "c.sqlite")))
    assert open_cache(cfg) is None


def test_tally_measures_one_stretch():
    c = Cache()
    tally = Tally(c)
    c.get("t", ["x"])
    tally.start()
    c.set("t", ["y"], 1)
    c.get("t", ["y"])
    c.get("t", ["z"])
    assert tally.delta() == {"hits": 1, "misses": 1}
    assert Tally(None).delta() == {"hits": 0, "misses": 0}


# -- the pipeline ------------------------------------------------------------------------------


def test_second_check_of_a_sentence_is_served_from_the_cache():
    inner, c = Fixed(answer="₹५०० दें।"), Cache()
    pipe = Pipeline(ChainTranslator([CachedTranslator(inner, c)], 5), cache=c)
    first = pipe.check("Pay ₹500.")
    second = pipe.check("Pay ₹500.")
    assert first.cache == {"hits": 0, "misses": 1} and second.cache == {"hits": 1, "misses": 0}
    assert len(inner.calls) == 1
    assert (first.final, first.report.codes()) == (second.final, second.report.codes())
    assert second.to_dict()["cache"] == {"hits": 1, "misses": 0}


def test_verification_still_runs_on_a_cached_translation():
    inner, c = Fixed(answer="₹५५० दें।"), Cache()  # wrong number, cached
    pipe = Pipeline(ChainTranslator([CachedTranslator(inner, c)], 5), cache=c)
    assert pipe.check("Pay ₹500.").report.status == "pass"  # repaired
    second = pipe.check("Pay ₹500.", None)
    assert second.before.codes() == ["NUMBER_CHANGED"] and second.repair.repaired


def test_a_pipeline_without_a_cache_reports_zero():
    r = Pipeline(None).check("Pay ₹500.", "₹५०० दें।")
    assert r.cache == {"hits": 0, "misses": 0}


def test_build_pipeline_wraps_every_translator_when_given_a_cache():
    cfg = load_config()
    c = Cache()
    pipe = build_pipeline(cfg, device="cpu", cache=c)
    assert pipe.cache is c
    assert all(isinstance(t, CachedTranslator) for t in pipe.chain.translators)
    assert [t.name for t in pipe.chain.translators][0] == cfg.models.en_indic
    assert build_pipeline(cfg, device="cpu").cache is None  # off in tests


def test_tone_scores_are_cached_too():
    inner = [Model("a"), Model("b"), Model("c")]
    c = Cache()
    analyzer = EmotionAnalyzer(
        *[CachedClassifier(m, c) for m in inner], settings=load_config().emotion
    )
    for m in inner:
        m.classify = lambda text, m=m: m.calls.append(text) or {"joy": 0.9, "irony": 0.1}
    analyzer.analyze_source("Great day!")
    analyzer.analyze_source("Great day!")
    assert [len(m.calls) for m in inner] == [1, 1, 1]


# -- command line ------------------------------------------------------------------------------


def test_cli_stats_and_clear(monkeypatch, tmp_path, capsys):
    path = tmp_path / "c.sqlite"
    cfg = replace(load_config(), cache=CacheSettings(True, str(path)))
    monkeypatch.setattr(cache_module, "load_config", lambda: cfg)
    assert cache_module.main(["stats"]) == 0 and "no cache yet" in capsys.readouterr().out
    c = Cache(path)
    c.set("translate", ["a"], "x")
    c.close()
    assert cache_module.main(["stats"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == {"translate": 1}
    assert cache_module.main(["clear"]) == 0 and "removed 1 entries" in capsys.readouterr().out
    assert cache_module.main(["wat"]) == 2 and cache_module.main([]) == 2


def test_the_database_is_plain_sqlite(tmp_path):
    path = tmp_path / "c.sqlite"
    c = Cache(path)
    c.set("t", ["x"], "y")
    rows = sqlite3.connect(path).execute("SELECT ns, value FROM kv").fetchall()
    assert rows == [("t", '"y"')]


# -- config ------------------------------------------------------------------------------------


def test_cache_settings_default_and_override(tmp_path):
    assert load_config().cache == CacheSettings(True, ".cache/meaningguard.sqlite")
    cfg = load_config(write(tmp_path, VALID + "cache:\n  enabled: false\n  path: x/y.sqlite\n"))
    assert cfg.cache == CacheSettings(False, "x/y.sqlite")


@pytest.mark.parametrize(
    "bad",
    ["cache: [1]\n", "cache:\n  enabled: maybe\n", "cache:\n  path: ''\n", "cache:\n  path: 3\n"],
)
def test_invalid_cache_settings(tmp_path, bad):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, VALID + bad))
