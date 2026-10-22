"""End-to-end smoke test with the real models: translate, verify, repair, fall back, time it.

    python tools/smoke_pipeline.py                  # IndicTrans2 and the Google fallback
    python tools/smoke_pipeline.py --skip-google    # no internet call to Google
    python tools/smoke_pipeline.py --device cpu
    python tools/smoke_pipeline.py --tone           # also load the emotion models (spec 08)

Writes reports/smoke_pipeline.json and exits non-zero if a check FAILs. The main checks use
IndicTrans2 only, so a broken model shows up as FAIL instead of being hidden by the fallback.
A verifier status of 'fail' on a real translation is a WARN here: it is a finding, not a bug.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meaningguard.config import load_config  # noqa: E402
from meaningguard.pipeline import ChainTranslator, Pipeline, TranslationFailed  # noqa: E402
from meaningguard.translator import GoogleFallback  # noqa: E402

SENTENCES = [
    "The price fell from Rs. 500 to Rs. 400, so do not pay after Friday.",
    "Pay ₹5 lakh by 30 June or a fine of ₹2,000 applies.",
    "If you pay at least ₹5,000 before 30 June, you get 10% off.",
    "The meeting starts at 10:30 AM on Monday.",
    "Orders above ₹1,000 ship free, otherwise shipping is ₹60.",
]
PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
SLOW_MS = 10_000  # a warm CPU translation slower than this is worth a warning


@dataclass
class Check:
    name: str
    status: str
    detail: str
    seconds: float = 0.0


class Broken:
    """A translator that always fails, to force the fallback."""

    name = "broken (forced failure)"

    def translate(self, sentences):
        raise RuntimeError("forced failure for the smoke test")


def _timed(name, fn) -> Check:
    start = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as exc:  # noqa: BLE001 - a crash is a FAIL, not an abort
        status, detail = FAIL, f"{type(exc).__name__}: {str(exc)[:200]}"
    return Check(name, status, detail, round(time.perf_counter() - start, 2))


def check_sentence(pipeline: Pipeline, source: str):
    def run():
        result = pipeline.check(source)
        json.dumps(result.to_dict(), ensure_ascii=False)  # must serialise
        again = pipeline.check(source, result.final)  # repair must be idempotent
        if again.repair.repaired or again.final != result.final:
            return FAIL, f"repair is not idempotent: {result.final!r} -> {again.final!r}"
        codes = ",".join(result.report.codes()) or "-"
        status = PASS if result.report.status in ("pass", "warn") else WARN
        edits = f", repaired {len(result.repair.edits)}" if result.repair.repaired else ""
        return status, f"{result.final} [{result.report.status}: {codes}{edits}]"

    return run


def run_checks(pipeline: Pipeline, cfg, skip_google: bool) -> tuple:
    checks: list = []
    warm: list = []
    for i, sentence in enumerate(SENTENCES):
        label = "cold (loads the model)" if i == 0 else "warm"
        check = _timed(
            f"translate+verify [{label}]: {sentence[:34]}", check_sentence(pipeline, sentence)
        )
        checks.append(check)
        if i > 0 and check.status != FAIL:
            warm.append(check.seconds)

    def repair_example():
        result = Pipeline(None).check("Pay ₹500.", "₹५५० दें।")
        ok = result.final == "₹५०० दें।" and result.report.status == "pass"
        return (PASS if ok else FAIL), f"{result.translation} -> {result.final}"

    checks.append(_timed("repair example (provided translation)", repair_example))

    def pins():
        missing = [m for m in (cfg.models.en_indic,) if m not in cfg.revisions]
        if missing:
            return WARN, "model versions not pinned: run tools/pin_models.py --write"
        return PASS, "pinned"

    checks.append(_timed("model versions pinned", pins))

    def fallback():
        chain = ChainTranslator([Broken(), GoogleFallback()], cfg.runtime.timeout_seconds)
        try:
            result = Pipeline(chain).check("Pay ₹500 by Friday.")
        except TranslationFailed as exc:
            text = str(exc).lower()
            if "too many requests" in text or "429" in text:  # the endpoint, not our code
                return WARN, "Google is rate limiting this IP; the chain reported it correctly"
            return FAIL, f"Google fallback failed too: {exc}"
        if "google" not in result.translator or not result.notes:
            return FAIL, f"fallback was not used: {result.translator}"
        return PASS, f"{result.translation} [{result.report.status}] via {result.translator}"

    checks.append(
        Check("fallback to Google (forced)", SKIP, "skipped", 0.0)
        if skip_google
        else _timed("fallback to Google (forced)", fallback)
    )
    if warm:
        slow = statistics.median(warm) * 1000 > SLOW_MS
        checks.append(
            Check(
                "latency",
                WARN if slow else PASS,
                f"warm median {statistics.median(warm):.2f} s, cold {checks[0].seconds:.2f} s",
            )
        )
    return checks, warm


TONE_PAIRS = [
    (
        "I am furious that they cancelled my flight without telling me.",
        "मैं बहुत नाराज़ हूँ कि उन्होंने मुझे बताए बिना मेरी उड़ान रद्द कर दी।",
        "negative",
    ),
    ("We won the match, I am so happy!", "हमने मैच जीत लिया, मैं बहुत खुश हूँ!", "positive"),
]


def tone_checks(checker) -> list:
    """Real emotion models on two clear sentences. A wrong tone is a WARN, not a bug."""
    checks = []
    for source, hindi, expected in TONE_PAIRS:

        def run(source=source, hindi=hindi, expected=expected):
            out = checker.check(source, hindi)
            if out.source is None:
                return FAIL, "; ".join(out.notes)
            got = (out.source["sentiment"], out.target["sentiment"] if out.target else None)
            codes = [f.code for f in out.findings]
            detail = f"source {got[0]}, translation {got[1]}, findings {codes}"
            if out.notes:
                detail += f"; notes: {'; '.join(out.notes)}"
            sincere = "SARCASM_POSSIBLY_LOST" not in codes  # neither sample is sarcastic
            ok = got[0] == expected and not out.notes and sincere
            return (PASS if ok else WARN), detail

        checks.append(_timed(f"tone: {source[:34]}", run))
    return checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default=None, help="cpu or cuda (default: from config)")
    parser.add_argument("--skip-google", action="store_true")
    parser.add_argument("--tone", action="store_true", help="also run the emotion models")
    parser.add_argument("--out", default=str(ROOT / "reports" / "smoke_pipeline.json"))
    args = parser.parse_args(argv)

    from meaningguard.pipeline import build_pipeline

    cfg = load_config()
    primary = build_pipeline(cfg, device=args.device).chain.translators[0]
    pipeline = Pipeline(ChainTranslator([primary], cfg.runtime.timeout_seconds))
    checks, _ = run_checks(pipeline, cfg, args.skip_google)
    if args.tone:
        from meaningguard.tone import build_tone_checker

        checks += tone_checks(build_tone_checker(cfg, args.device))
    for c in checks:
        print(f"{c.status.upper():5} {c.seconds:7.2f}s  {c.name}\n         {c.detail}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps([asdict(c) for c in checks], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    failed = sum(c.status == FAIL for c in checks)
    print(f"\n{failed} failed, {sum(c.status == WARN for c in checks)} warnings -> {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
