"""Emotion and sarcasm (spec 08): pretrained classifiers and a deterministic voting rule.

Nothing is trained. A classifier is any object with ``classify(text) -> {label: probability}``;
``HfClassifier`` wraps a Hugging Face pipeline and loads it on first use. The voting, the
sarcasm rule and the record are plain functions of the classifier outputs, so they are tested
without any model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, EmotionSettings, load_config

SENTIMENTS = ("negative", "neutral", "positive")
EMOTIONS = ("anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise")
IRONY = ("non_irony", "irony")
# 'surprise' can be pleasant or not: it abstains from the sentiment vote.
EMOTION_SENTIMENT = {
    "anger": "negative",
    "disgust": "negative",
    "fear": "negative",
    "sadness": "negative",
    "joy": "positive",
    "neutral": "neutral",
}
LABELS = {"sentiment": SENTIMENTS, "emotion": EMOTIONS, "irony": IRONY}


@dataclass(frozen=True)
class Signal:
    name: str
    label: str
    prob: float
    sentiment: str | None  # what this signal says about polarity; None = it abstains

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "prob": round(self.prob, 3),
            "sentiment": self.sentiment,
        }


def canonical_scores(kind: str, raw: list) -> dict:
    """``[{'label', 'score'}, ...]`` from a pipeline to ``{canonical label: probability}``."""
    names = LABELS[kind]
    out: dict = {}
    for item in raw:
        label = str(item["label"]).lower()
        if label.startswith("label_"):  # models published without label names
            label = names[int(label[6:])]
        if label not in names:
            raise ValueError(f"unexpected {kind} label {item['label']!r}; expected one of {names}")
        out[label] = float(item["score"])
    return out


@dataclass
class HfClassifier:
    model_id: str
    kind: str  # sentiment, emotion or irony
    revision: str | None = None
    device: str = "cpu"
    max_length: int = 128
    _pipe: object = field(default=None, init=False, repr=False)

    def _load(self):
        from transformers import pipeline

        return pipeline(
            "text-classification",
            model=self.model_id,
            revision=self.revision,
            top_k=None,
            device=0 if self.device == "cuda" else -1,
        )

    def classify(self, text: str) -> dict:
        if self._pipe is None:
            self._pipe = self._load()
        raw = self._pipe(text, truncation=True, max_length=self.max_length)
        if raw and isinstance(raw[0], list):
            raw = raw[0]
        return canonical_scores(self.kind, raw)

    def close(self) -> None:
        self._pipe = None


def top(scores: dict) -> tuple:
    label = max(scores, key=scores.get)
    return label, scores[label]


def vote(signals: list, settings: EmotionSettings) -> tuple:
    """``(sentiment, confidence, votes)``. At least two confident signals must agree.

    Confidence is the summed probability of the agreeing signals divided by the number of signals
    that state a polarity, so a dissenting or hesitant signal lowers it."""
    stated = [s for s in signals if s.sentiment is not None]
    usable = [s for s in stated if s.prob >= settings.min_prob]
    if len(usable) < 2:
        return "uncertain", 0.0, 0
    tally: dict = {}
    for s in usable:
        count, total = tally.get(s.sentiment, (0, 0.0))
        tally[s.sentiment] = (count + 1, total + s.prob)
    winner = max(tally, key=lambda k: tally[k])
    count, total = tally[winner]
    if count < 2:
        return "uncertain", 0.0, count
    return winner, total / len(stated), count


def _signal(prefix: str, text_scores: dict, kind: str) -> Signal:
    label, prob = top(text_scores)
    sentiment = label if kind == "sentiment" else EMOTION_SENTIMENT.get(label)
    return Signal(f"{kind}:{prefix}", label, prob, sentiment)


def is_sarcastic(irony_prob: float, surface: Signal | None, emotion: Signal | None, s) -> bool:
    """Irony score alone, or a lower score when the words sound positive but the emotion is not."""
    if irony_prob >= s.irony_prob:
        return True
    conflict = (
        surface is not None
        and emotion is not None
        and surface.sentiment == "positive"
        and emotion.sentiment == "negative"
        and surface.prob >= s.min_prob
        and emotion.prob >= s.min_prob
    )
    return conflict and irony_prob >= s.irony_low


def _record(signals, label, sarcasm, settings, extra=()) -> dict:
    sentiment, confidence, votes = vote(signals, settings)
    return {
        "sentiment": sentiment,
        "label": label,
        "possible_sarcasm": sarcasm,
        "confidence": round(confidence, 3),
        "signals": [s.to_dict() for s in [*signals, *extra]],
        "votes": votes,
    }


def _uncertain() -> dict:
    return {
        "sentiment": "uncertain",
        "label": "uncertain",
        "possible_sarcasm": False,
        "confidence": 0.0,
        "signals": [],
        "votes": 0,
    }


class EmotionAnalyzer:
    def __init__(self, sentiment, emotion, irony, settings: EmotionSettings | None = None):
        self.sentiment, self.emotion, self.irony = sentiment, emotion, irony
        self.settings = settings or EmotionSettings()

    def _label(self, emotion_signal: Signal | None) -> str:
        if emotion_signal is None or emotion_signal.prob < self.settings.label_prob:
            return "uncertain"
        return emotion_signal.label

    def analyze_source(self, text: str) -> dict:
        """English source: sentiment, emotion and irony on the text itself."""
        if not text.strip():
            return _uncertain()
        surface = _signal("xlmr", self.sentiment.classify(text), "sentiment")
        emotion = _signal("distilroberta", self.emotion.classify(text), "emotion")
        irony_prob = self.irony.classify(text).get("irony", 0.0)
        extra = [Signal("irony:roberta", "irony", irony_prob, None)]
        sarcasm = is_sarcastic(irony_prob, surface, emotion, self.settings)
        return _record([surface, emotion], self._label(emotion), sarcasm, self.settings, extra)

    def analyze_target(self, text: str, english: str | None = None) -> dict:
        """Hindi translation: sentiment on the Hindi, and sentiment plus emotion on its English
        back-translation when there is one. Sarcasm is not judged on the translation."""
        if not text.strip():
            return _uncertain()
        signals = [_signal("xlmr-hi", self.sentiment.classify(text), "sentiment")]
        emotion = None
        if english and english.strip():
            signals.append(_signal("xlmr-back", self.sentiment.classify(english), "sentiment"))
            emotion = _signal("distilroberta-back", self.emotion.classify(english), "emotion")
            signals.append(emotion)
        return _record(signals, self._label(emotion), None, self.settings)

    def close(self) -> None:
        for model in (self.sentiment, self.emotion, self.irony):
            close = getattr(model, "close", None)
            if close:
                close()


def build_analyzer(cfg: Config | None = None, device: str | None = None) -> EmotionAnalyzer:
    """The three pinned pretrained models; nothing is downloaded until the first sentence."""
    from .check_models import resolve_device

    cfg = cfg or load_config()
    device = device or resolve_device(cfg.runtime.device)

    def make(model_id: str, kind: str) -> HfClassifier:
        return HfClassifier(
            model_id, kind, cfg.revisions.get(model_id), device, cfg.emotion.max_length
        )

    return EmotionAnalyzer(
        make(cfg.models.sentiment, "sentiment"),
        make(cfg.models.emotion_en, "emotion"),
        make(cfg.models.irony_en, "irony"),
        cfg.emotion,
    )
