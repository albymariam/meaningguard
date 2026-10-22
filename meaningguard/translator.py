"""IndicTrans2 wrapper: loads a model once, translates in batches (pretrained, no training).

Phase 4 adds the fallback chain, caching and timeouts on top of this class.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config


@dataclass
class Translator:
    cfg: Config
    model_id: str
    src: str
    tgt: str
    device: str = "cpu"
    batch_size: int = 8
    _loaded: tuple | None = field(default=None, init=False, repr=False)

    def _load(self) -> tuple:
        from IndicTransToolkit import IndicProcessor
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        processor = IndicProcessor(inference=True)
        tokenizer = AutoTokenizer.from_pretrained(self.model_id, trust_remote_code=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(self.model_id, trust_remote_code=True)
        return processor, tokenizer, model.to(self.device)

    def _ensure_loaded(self) -> tuple:
        if self._loaded is None:
            self._loaded = self._load()
        return self._loaded

    def _generate(self, model, encoded) -> object:
        import torch

        runtime = self.cfg.runtime
        with torch.inference_mode():
            return model.generate(
                **encoded,
                num_beams=runtime.num_beams,
                num_return_sequences=1,
                use_cache=runtime.use_cache,
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
