"""IndicTrans2 wrapper (pretrained, no training) and the Google fallback.

``Translator`` loads a model once and translates in batches. ``GoogleFallback`` is the unofficial
Google endpoint through deep-translator: no key and no download, used only when IndicTrans2 fails.
Both have ``name`` and ``translate(list[str]) -> list[str]``; ``pipeline.ChainTranslator``
tries them in order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config


def _is_cache_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(word in text for word in ("shape", "cache", "past_key"))


@dataclass
class Translator:
    cfg: Config
    model_id: str
    src: str
    tgt: str
    device: str = "cpu"
    batch_size: int = 8
    revision: str | None = None  # pinned commit of the model, from config.revisions
    _loaded: tuple | None = field(default=None, init=False, repr=False)
    _use_cache: bool | None = field(default=None, init=False, repr=False)  # set by a cache failure

    @property
    def name(self) -> str:
        return self.model_id

    @property
    def cache_key(self) -> tuple:
        """Everything that changes the output for a given sentence (not the device)."""
        runtime = self.cfg.runtime
        return (
            self.model_id,
            self.revision,
            self.src,
            self.tgt,
            runtime.num_beams,
            runtime.max_length,
        )

    def _load(self) -> tuple:
        from IndicTransToolkit import IndicProcessor
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        processor = IndicProcessor(inference=True)
        options = {"trust_remote_code": True, "revision": self.revision}
        tokenizer = AutoTokenizer.from_pretrained(self.model_id, **options)
        model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id, **options)
        return processor, tokenizer, model.to(self.device)

    def _ensure_loaded(self) -> tuple:
        if self._loaded is None:
            self._loaded = self._load()
        return self._loaded

    def _generate(self, model, encoded) -> object:
        """Generate with the configured cache setting. IndicTrans2's remote code can fail inside
        ``generate`` with a key-value cache error (``'NoneType' ... 'shape'``) on some transformers
        versions; the first such failure switches the cache off for this translator and retries."""
        use_cache = self.cfg.runtime.use_cache if self._use_cache is None else self._use_cache
        try:
            return self._run(model, encoded, use_cache)
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            if not use_cache or not _is_cache_error(exc):
                raise
            self._use_cache = False
            return self._run(model, encoded, False)

    def _run(self, model, encoded, use_cache: bool) -> object:
        import torch

        runtime = self.cfg.runtime
        with torch.inference_mode():
            return model.generate(
                **encoded,
                num_beams=runtime.num_beams,
                num_return_sequences=1,
                use_cache=use_cache,
                max_length=runtime.max_length,
            )

    def translate(self, sentences: list[str]) -> list[str]:
        if not sentences:
            return []
        processor, tokenizer, model = self._ensure_loaded()
        results: list[str] = []
        for start in range(0, len(sentences), self.batch_size):
            chunk = sentences[start : start + self.batch_size]
            batch = processor.preprocess_batch(chunk, src_lang=self.src, tgt_lang=self.tgt)
            encoded = tokenizer(
                batch,
                padding="longest",
                truncation=True,
                max_length=self.cfg.runtime.max_length,
                return_tensors="pt",
            ).to(self.device)
            generated = self._generate(model, encoded)
            decoded = tokenizer.batch_decode(
                generated, skip_special_tokens=True, clean_up_tokenization_spaces=True
            )
            results.extend(processor.postprocess_batch(decoded, lang=self.tgt))
        return results

    def close(self) -> None:
        self._loaded = None
        import gc

        gc.collect()


@dataclass
class GoogleFallback:
    """deep-translator's Google endpoint, rate limited, so a failure is retried with backoff."""

    source: str = "en"
    target: str = "hi"
    attempts: int = 3
    first_wait: float = 1.0

    @property
    def name(self) -> str:
        return "google (deep-translator)"

    @property
    def cache_key(self) -> tuple:
        return ("google", self.source, self.target)

    def translate(self, sentences: list[str]) -> list[str]:
        import time

        from deep_translator import GoogleTranslator

        client = GoogleTranslator(source=self.source, target=self.target)
        out: list[str] = []
        for sentence in sentences:
            wait, last = self.first_wait, None
            for attempt in range(1, self.attempts + 1):
                try:
                    out.append(client.translate(sentence))
                    break
                except Exception as exc:  # noqa: BLE001 - network and rate-limit errors vary
                    last = exc
                    if attempt < self.attempts:
                        time.sleep(wait)
                        wait *= 2
            else:
                raise RuntimeError(f"Google translation failed: {type(last).__name__}: {last}")
        return out

    def close(self) -> None:
        return None
