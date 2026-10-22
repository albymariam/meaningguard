"""Verify the environment and every model the pipeline depends on.

Usage:
    python -m meaningguard.check_models                 # environment, packages, model access
    python -m meaningguard.check_models --full          # also load each model and run one inference
    python -m meaningguard.check_models --full --network  # also test the Google fallback translator

Writes a JSON report (default: reports/model_check.json) and exits non-zero if any check fails.
Heavy libraries are imported lazily so the module itself imports without torch installed.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import json
import os
import platform
import re
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from meaningguard.config import Config, ConfigError, load_config

MIN_PYTHON = (3, 10)
RECOMMENDED = {"torch": "2.5", "transformers": "4.51", "numpy": "2.1"}
MAX_EXCLUSIVE = {"transformers": "5"}

# import name -> pip name
REQUIRED_MODULES = {
    "torch": "torch",
    "numpy": "numpy",
    "transformers": "transformers",
    "sentencepiece": "sentencepiece",
    "huggingface_hub": "huggingface_hub",
    "IndicTransToolkit": "indictranstoolkit",
    "deep_translator": "deep-translator",
    "streamlit": "streamlit",
    "yaml": "pyyaml",
}

EN_SMOKE = "The price fell from Rs. 500 to Rs. 400, so do not pay after Friday."
HI_SMOKE = "मुझे यह बिल्कुल पसंद नहीं आया।"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
LATIN = re.compile(r"[A-Za-z]")

PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0


def error_location(exc: BaseException, frames: int = 3) -> str:
    """Return the innermost stack frames of an exception as 'file:line in function'."""
    stack = traceback.extract_tb(exc.__traceback__)[-frames:]
    return " <- ".join(f"{Path(f.filename).name}:{f.lineno} in {f.name}" for f in reversed(stack))


def _timed(name: str, fn: Callable[[], tuple[str, str]]) -> Check:
    start = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a failed check must never abort the run
        where = error_location(exc)
        status = FAIL
        detail = f"{type(exc).__name__}: {str(exc)[:300]}" + (f" @ {where}" if where else "")
    return Check(name, status, detail, round(time.perf_counter() - start, 2))


def check_python() -> Check:
    current = sys.version_info[:3]
    text = ".".join(map(str, current))
    if current < MIN_PYTHON + (0,):
        return Check("python", FAIL, f"{text}; need >= {'.'.join(map(str, MIN_PYTHON))}")
    return Check("python", PASS, text)


def check_platform() -> Check:
    system = platform.system()
    if system == "Windows":
        return Check(
            "platform",
            WARN,
            "IndicTransToolkit has no Windows wheel and is not tested on Windows; "
            "run translation in WSL2 or Colab",
        )
    return Check("platform", PASS, f"{system} {platform.machine()}")


def check_modules() -> tuple[list[Check], dict[str, str]]:
    checks: list[Check] = []
    versions: dict[str, str] = {}
    for module_name, pip_name in REQUIRED_MODULES.items():
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}; pip install {pip_name}"
            status = FAIL
            if module_name == "IndicTransToolkit" and platform.system() == "Windows":
                status = WARN
                detail = (
                    "not installed; no Windows wheel on PyPI. Use WSL2 or Colab, "
                    "or install Microsoft C++ Build Tools and run pip install indictranstoolkit"
                )
            checks.append(Check(f"package:{module_name}", status, detail))
            continue
        version = str(getattr(module, "__version__", "unknown"))
        versions[module_name] = version
        checks.append(Check(f"package:{module_name}", PASS, version))
    return checks, versions


def check_versions(versions: dict[str, str]) -> list[Check]:
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return [Check("versions", SKIP, "packaging not installed")]

    checks: list[Check] = []
    for name, minimum in RECOMMENDED.items():
        found = versions.get(name)
        if found is None or found == "unknown":
            continue
        try:
            parsed = Version(found.split("+")[0])
        except InvalidVersion:
            checks.append(Check(f"version:{name}", WARN, f"unparseable version {found}"))
            continue
        upper = MAX_EXCLUSIVE.get(name)
        if parsed < Version(minimum):
            checks.append(Check(f"version:{name}", WARN, f"{found} < recommended {minimum}"))
        elif upper and parsed >= Version(upper):
            detail = f"{found} >= {upper}; this project pins <{upper}"
            checks.append(Check(f"version:{name}", WARN, detail))
        else:
            checks.append(Check(f"version:{name}", PASS, found))
    return checks


def find_hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        return token
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:  # noqa: BLE001
        return None


def _validate_token(token: str) -> tuple[str, str]:
    """Ask huggingface.co who owns the token, so a placeholder or revoked token is caught."""
    from huggingface_hub import whoami

    try:
        info = whoami(token=token)
    except Exception as exc:  # noqa: BLE001
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 401:
            return FAIL, (
                "huggingface.co rejected this token (invalid, revoked or a placeholder). "
                "Create a Read token and set HF_TOKEN; note HF_TOKEN overrides `hf auth login`"
            )
        return WARN, f"could not verify the token ({type(exc).__name__}); network problem?"
    role = (info.get("auth") or {}).get("accessToken", {}).get("role")
    detail = f"valid; account={info.get('name')}; role={role}"
    if role == "fineGrained":
        return WARN, detail + "; fine-grained tokens need explicit access to each gated repo"
    return PASS, detail


def check_hf_token(token: str | None) -> Check:
    if token:
        return _timed("hf_token", lambda: _validate_token(token))
    return Check(
        "hf_token",
        FAIL,
        "no token; the IndicTrans2 models are gated. Create a read token and set HF_TOKEN",
    )


def explain_hf_error(model_id: str, exc: Exception) -> str:
    kind = type(exc).__name__
    if kind == "GatedRepoError":
        return (
            f"gated: this token cannot read {model_id}. Open https://huggingface.co/{model_id}, "
            "accept the terms with the same account, and put a Read token in HF_TOKEN"
        )
    if kind == "RepositoryNotFoundError":
        return "repository not found, or private and not visible to this token"
    if kind in {"LocalEntryNotFoundError", "ConnectionError", "ConnectTimeout", "ProxyError"}:
        return f"cannot reach huggingface.co ({kind})"
    return f"{kind}: {str(exc)[:200]}"


def check_model_access(model_id: str, token: str | None) -> Check:
    """Send real HEAD requests for config.json and, where needed, the remote-code file.

    hf_hub_download is avoided on purpose: when the network fails it silently returns a cached
    copy, which made this check pass without proving access.
    """
    start = time.perf_counter()
    files = ["config.json"]
    if model_id.startswith("ai4bharat/indictrans2"):
        files.append("modeling_indictrans.py")  # loaded with trust_remote_code
    try:
        from huggingface_hub import get_hf_file_metadata, hf_hub_url

        for filename in files:
            get_hf_file_metadata(hf_hub_url(model_id, filename), token=token)
        status, detail = PASS, "reachable with this token: " + ", ".join(files)
    except Exception as exc:  # noqa: BLE001
        status, detail = FAIL, explain_hf_error(model_id, exc)
    return Check(f"access:{model_id}", status, detail, round(time.perf_counter() - start, 2))


def resolve_device(preference: str) -> str:
    if preference != "auto":
        return preference
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _free_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def translate(
    cfg: Config, model_id: str, sentences: list[str], src: str, tgt: str, device: str
) -> list[str]:
    import torch
    from IndicTransToolkit import IndicProcessor
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    processor = IndicProcessor(inference=True)
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id, trust_remote_code=True).to(device)
    try:
        batch = processor.preprocess_batch(sentences, src_lang=src, tgt_lang=tgt)
        encoded = tokenizer(
            batch,
            padding="longest",
            truncation=True,
            max_length=cfg.runtime.max_length,
            return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                num_beams=cfg.runtime.num_beams,
                num_return_sequences=1,
                use_cache=cfg.runtime.use_cache,
                max_length=cfg.runtime.max_length,
            )
        decoded = tokenizer.batch_decode(
            generated, skip_special_tokens=True, clean_up_tokenization_spaces=True
        )
        return processor.postprocess_batch(decoded, lang=tgt)
    finally:
        del model
        _free_memory()


def check_translation(cfg: Config, device: str) -> list[Check]:
    src, tgt = cfg.languages.source, cfg.languages.target
    state: dict[str, str] = {}

    def forward() -> tuple[str, str]:
        out = translate(cfg, cfg.models.en_indic, [EN_SMOKE], src, tgt, device)[0]
        state["hindi"] = out
        if not DEVANAGARI.search(out):
            return FAIL, f"no Devanagari in output: {out!r}"
        return PASS, out

    def backward() -> tuple[str, str]:
        source_text = state.get("hindi")
        if not source_text:
            return SKIP, "forward translation did not produce text"
        out = translate(cfg, cfg.models.indic_en, [source_text], tgt, src, device)[0]
        if not LATIN.search(out):
            return FAIL, f"no Latin text in output: {out!r}"
        return PASS, out

    return [
        _timed(f"translate:{cfg.models.en_indic}", forward),
        _timed(f"translate:{cfg.models.indic_en}", backward),
    ]


def classify(model_id: str, texts: list[str], device: str) -> list[list[dict]]:
    from transformers import pipeline

    classifier = pipeline(
        "text-classification", model=model_id, device=0 if device == "cuda" else -1, top_k=None
    )
    try:
        return classifier(texts)
    finally:
        del classifier
        _free_memory()


def check_classifier(model_id: str, text: str, device: str) -> Check:
    def run() -> tuple[str, str]:
        scores = classify(model_id, [text], device)[0]
        if not scores:
            return FAIL, "no labels returned"
        total = sum(item["score"] for item in scores)
        if not 0.98 <= total <= 1.02:
            return FAIL, f"scores sum to {total:.3f}, expected about 1"
        top = max(scores, key=lambda item: item["score"])
        labels = sorted(item["label"] for item in scores)
        return PASS, f"top={top['label']} ({top['score']:.2f}); labels={labels}"

    return _timed(f"classify:{model_id}", run)


def check_google_fallback(attempts: int = 4, first_wait: float = 3.0) -> Check:
    """The Google endpoint is rate limited and unofficial, so retry with backoff."""

    def run() -> tuple[str, str]:
        from deep_translator import GoogleTranslator

        wait, last = first_wait, ""
        for attempt in range(1, attempts + 1):
            try:
                out = GoogleTranslator(source="en", target="hi").translate(EN_SMOKE)
            except Exception as exc:  # noqa: BLE001 - network and rate-limit errors vary
                last = f"{type(exc).__name__}: {str(exc)[:120]}"
            else:
                if out and DEVANAGARI.search(out):
                    return PASS, f"{out} (attempt {attempt})"
                last = f"unexpected output: {out!r}"
            if attempt < attempts:
                time.sleep(wait)
                wait *= 2
        return FAIL, f"failed after {attempts} attempts; last error: {last}"

    return _timed("fallback:google", run)


def print_table(checks: list[Check]) -> None:
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status.upper():4}] {c.name:<{width}}  {c.detail}  ({c.seconds}s)")


def build_report(checks: list[Check], versions: dict[str, str], device: str) -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "device": device,
        "versions": versions,
        "checks": [asdict(c) for c in checks],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the environment and models.")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("--full", action="store_true", help="load models and run one inference")
    parser.add_argument("--network", action="store_true", help="also test the Google fallback")
    parser.add_argument("--report", default="reports/model_check.json", help="JSON report path")
    parser.add_argument(
        "--only",
        choices=["access", "translate", "classify", "fallback"],
        help="run just one group of checks (translate, classify and fallback imply their flag)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="generate with use_cache=False (workaround for key-value cache errors; slower)",
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    if args.no_cache:
        cfg = replace(cfg, runtime=replace(cfg.runtime, use_cache=False))

    only = args.only
    checks: list[Check] = [check_python(), check_platform()]
    module_checks, versions = check_modules()
    checks += module_checks
    checks += check_versions(versions)

    token = find_hf_token()
    checks.append(check_hf_token(token))
    if only in (None, "access"):
        for model_id in (
            cfg.models.en_indic,
            cfg.models.indic_en,
            cfg.models.sentiment,
            cfg.models.emotion_en,
            cfg.models.irony_en,
        ):
            checks.append(check_model_access(model_id, token))

    device = resolve_device(cfg.runtime.device)
    if (args.full and only is None) or only == "translate":
        checks += check_translation(cfg, device)
    if (args.full and only is None) or only == "classify":
        checks.append(check_classifier(cfg.models.sentiment, EN_SMOKE, device))
        checks.append(check_classifier(cfg.models.sentiment, HI_SMOKE, device))
        checks.append(check_classifier(cfg.models.emotion_en, EN_SMOKE, device))
        checks.append(check_classifier(cfg.models.irony_en, EN_SMOKE, device))
    if (args.network and only is None) or only == "fallback":
        checks.append(check_google_fallback())

    print_table(checks)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(build_report(checks, versions, device), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failed = [c for c in checks if c.status == FAIL]
    print(f"\n{len(failed)} failed, report written to {report_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
