"""Voting, labels, sarcasm and the Hugging Face wrapper, with fake classifiers."""

import sys
from types import SimpleNamespace

import pytest

from meaningguard.config import ConfigError, EmotionSettings, load_config
from meaningguard.emotion import (
    EmotionAnalyzer,
    HfClassifier,
    Signal,
    build_analyzer,
    canonical_scores,
    is_sarcastic,
    vote,
)

S = EmotionSettings()


class Fake:
    """classify(text) -> scores looked up by exact text, else by the default."""

    def __init__(self, table=None, default=None):
        self.table, self.default, self.seen = table or {}, default or {}, []

    def classify(self, text):
        self.seen.append(text)
        return self.table.get(text, self.default)


def sig(sentiment, prob, name="x"):
    return Signal(name, sentiment or "surprise", prob, sentiment)


# -- voting ------------------------------------------------------------------------------------


def test_two_agreeing_signals_win_and_confidence_is_their_mean():
    assert vote([sig("negative", 0.9), sig("negative", 0.7)], S) == ("negative", 0.8, 2)


def test_disagreement_is_uncertain():
    assert vote([sig("positive", 0.9), sig("negative", 0.9)], S)[0] == "uncertain"


def test_a_hesitant_signal_abstains():
    assert vote([sig("negative", 0.9), sig("negative", 0.3)], S) == ("uncertain", 0.0, 0)


def test_one_signal_is_never_enough():
    assert vote([sig("positive", 0.99)], S)[0] == "uncertain"
    assert vote([], S) == ("uncertain", 0.0, 0)


def test_a_dissenting_signal_lowers_the_confidence():
    sentiment, confidence, votes = vote(
        [sig("negative", 0.9), sig("negative", 0.9), sig("positive", 0.5)], S
    )
    assert (sentiment, votes) == ("negative", 2) and confidence == pytest.approx(1.8 / 3)


def test_surprise_abstains_from_the_vote():
    assert vote([sig("positive", 0.9), sig(None, 0.9)], S)[0] == "uncertain"


def test_a_tie_goes_to_the_more_confident_side():
    signals = [
        sig("positive", 0.9),
        sig("positive", 0.8),
        sig("negative", 0.6),
        sig("negative", 0.5),
    ]
    assert vote(signals, S)[0] == "positive"


# -- sarcasm -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "irony,surface,emotion,expected",
    [
        (0.95, sig("positive", 0.9), sig("joy", 0.9), True),  # the irony score alone
        (0.6, sig("positive", 0.9), sig("joy", 0.9), False),  # not ironic enough
        (0.99, sig("neutral", 0.9), sig("neutral", 0.9), False),  # no stance, nothing to mock
        (0.99, sig("neutral", 0.9), sig("joy", 0.9), True),
        (0.4, sig("positive", 0.8), sig("negative", 0.8), True),  # positive words, negative feeling
        (0.4, sig("negative", 0.8), sig("negative", 0.8), False),
        (0.2, sig("positive", 0.8), sig("negative", 0.8), False),  # below the low threshold
        (0.4, sig("positive", 0.3), sig("negative", 0.8), False),  # the surface signal hesitates
    ],
)
def test_is_sarcastic(irony, surface, emotion, expected):
    assert is_sarcastic(irony, surface, emotion, S) is expected


# -- the analyzer ------------------------------------------------------------------------------


def analyzer(sentiment, emotion, irony):
    return EmotionAnalyzer(Fake(*sentiment), Fake(*emotion), Fake(*irony), S)


ANGRY = ({}, {"negative": 0.9, "neutral": 0.08, "positive": 0.02})
ANGER = ({}, {"anger": 0.8, "disgust": 0.1, "neutral": 0.1})
CALM = ({}, {"non_irony": 0.9, "irony": 0.1})


def test_source_record():
    rec = analyzer(ANGRY, ANGER, CALM).analyze_source("I am furious.")
    assert (rec["sentiment"], rec["label"], rec["possible_sarcasm"], rec["votes"]) == (
        "negative",
        "anger",
        False,
        2,
    )
    assert rec["confidence"] == pytest.approx(0.85)
    assert [s["name"] for s in rec["signals"]] == [
        "sentiment:xlmr",
        "emotion:distilroberta",
        "irony:roberta",
    ]


def test_low_emotion_probability_gives_no_label():
    weak = ({}, {"anger": 0.45, "disgust": 0.4, "neutral": 0.15})
    rec = analyzer(ANGRY, weak, CALM).analyze_source("Hmm.")
    assert rec["label"] == "uncertain" and rec["sentiment"] == "negative"


def test_sarcastic_source():
    pos = ({}, {"negative": 0.05, "neutral": 0.15, "positive": 0.8})
    disgust = ({}, {"disgust": 0.7, "neutral": 0.3})
    ironic = ({}, {"non_irony": 0.6, "irony": 0.4})
    rec = analyzer(pos, disgust, ironic).analyze_source("Oh great, another Monday meeting.")
    assert rec["possible_sarcasm"] is True and rec["sentiment"] == "uncertain"


def test_target_uses_the_hindi_text_and_its_back_translation():
    sent = Fake({"मैं गुस्से में हूँ": {"negative": 0.8, "neutral": 0.2}}, ANGRY[1])
    emo = Fake({}, ANGER[1])
    rec = EmotionAnalyzer(sent, emo, Fake(), S).analyze_target("मैं गुस्से में हूँ", "I am angry")
    assert rec["sentiment"] == "negative" and rec["label"] == "anger"
    assert rec["possible_sarcasm"] is None and rec["votes"] == 3
    assert sent.seen == ["मैं गुस्से में हूँ", "I am angry"] and emo.seen == ["I am angry"]


def test_target_without_a_back_translation_is_uncertain_by_design():
    rec = analyzer(ANGRY, ANGER, CALM).analyze_target("मैं गुस्से में हूँ")
    assert rec["sentiment"] == "uncertain" and rec["label"] == "uncertain" and rec["votes"] == 0


def test_empty_text_does_not_call_the_models():
    sent, emo, irony = Fake(), Fake(), Fake()
    rec = EmotionAnalyzer(sent, emo, irony, S).analyze_source("   ")
    assert rec["sentiment"] == "uncertain" and not (sent.seen or emo.seen or irony.seen)


def test_record_is_json():
    import json

    json.dumps(analyzer(ANGRY, ANGER, CALM).analyze_source("x"))


# -- the Hugging Face wrapper ------------------------------------------------------------------


def test_canonical_scores_names_and_indices():
    assert canonical_scores("sentiment", [{"label": "Positive", "score": 0.6}]) == {"positive": 0.6}
    assert canonical_scores("sentiment", [{"label": "LABEL_0", "score": 1}]) == {"negative": 1.0}
    assert canonical_scores("irony", [{"label": "LABEL_1", "score": 0.7}]) == {"irony": 0.7}
    with pytest.raises(ValueError, match="unexpected emotion label"):
        canonical_scores("emotion", [{"label": "boredom", "score": 1}])


def fake_transformers(monkeypatch, output):
    built = []

    def factory(task, **options):
        built.append((task, options))
        return lambda text, **kw: output

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=factory))
    return built


def test_hf_classifier_loads_once_with_the_pinned_revision(monkeypatch):
    built = fake_transformers(monkeypatch, [[{"label": "joy", "score": 0.9}]])  # nested form
    clf = HfClassifier("m/emotion", "emotion", revision="abc", device="cpu")
    assert clf.classify("hi") == {"joy": 0.9} and clf.classify("again") == {"joy": 0.9}
    assert len(built) == 1
    task, options = built[0]
    assert task == "text-classification" and options["revision"] == "abc"
    assert options["top_k"] is None and options["device"] == -1


def test_hf_classifier_flat_output_and_cuda(monkeypatch):
    built = fake_transformers(monkeypatch, [{"label": "irony", "score": 0.4}])
    clf = HfClassifier("m/irony", "irony", device="cuda")
    assert clf.classify("x") == {"irony": 0.4} and built[0][1]["device"] == 0


def test_build_analyzer_uses_config_models_revisions_and_thresholds():
    cfg = load_config()
    pinned = SimpleNamespace(
        models=cfg.models,
        runtime=cfg.runtime,
        revisions={cfg.models.irony_en: "r1"},
        emotion=EmotionSettings(irony_prob=0.9, max_length=64),
        cache=cfg.cache,
    )
    a = build_analyzer(pinned, device="cpu")
    assert [a.sentiment.model_id, a.emotion.model_id, a.irony.model_id] == [
        cfg.models.sentiment,
        cfg.models.emotion_en,
        cfg.models.irony_en,
    ]
    assert a.irony.revision == "r1" and a.sentiment.revision is None
    assert a.settings.irony_prob == 0.9 and a.irony.max_length == 64


# -- config ------------------------------------------------------------------------------------


def test_default_emotion_settings():
    assert load_config().emotion == EmotionSettings()


@pytest.mark.parametrize(
    "bad", ["emotion: [1]\n", "emotion:\n  irony_prob: 1.5\n", "emotion:\n  min_prob: x\n"]
)
def test_invalid_emotion_settings(tmp_path, bad):
    from tests.test_config import VALID, write

    with pytest.raises(ConfigError):
        load_config(write(tmp_path, VALID + bad))


def test_emotion_settings_can_be_overridden(tmp_path):
    from tests.test_config import VALID, write

    cfg = load_config(write(tmp_path, VALID + "emotion:\n  irony_prob: 0.7\n  max_length: 64\n"))
    assert (cfg.emotion.irony_prob, cfg.emotion.max_length, cfg.emotion.min_prob) == (0.7, 64, 0.4)


def test_neutral_guard_can_be_switched_off():
    from dataclasses import replace

    neutral = sig("neutral", 0.9)
    assert is_sarcastic(0.95, neutral, neutral, S) is False
    assert is_sarcastic(0.95, neutral, neutral, replace(S, neutral_guard=False)) is True
