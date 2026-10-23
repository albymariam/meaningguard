"""MeaningGuard: check that an English-to-Hindi translation keeps the facts of the source.

python -m streamlit run app.py
"""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from pathlib import Path

import streamlit as st

from meaningguard import pipeline as mg
from meaningguard import render
from meaningguard.config import load_config
from meaningguard.lexer import LexError

# (button label, English, Hindi translation to check or None to translate)
EXAMPLES = [
    ("Wrong number", "Pay ₹500 by Friday.", "शुक्रवार तक ₹५५० का भुगतान करें।"),
    ("Wrong currency", "The ticket costs $45.", "टिकट की कीमत ₹४५ है।"),
    ("Dropped negation", "Do not pay after Friday.", "शुक्रवार के बाद भुगतान करें।"),
    ("Flipped direction", "Sales rose by 20% last year.", "पिछले साल बिक्री में 20% की कमी आई।"),
    ("Translate it", "If you pay at least ₹5,000 before 30 June, you get 10% off.", None),
    ("Sarcasm", "Oh great, another Monday morning meeting.", None),
]
TONE_DEFAULT = os.environ.get("MEANINGGUARD_TONE", "1") != "0"  # the Space sets it to 0
THEME = Path(__file__).parent / "meaningguard" / "theme.css"
HERO = """<div class="mg-hero"><div>
<h1 class="mg-title">MeaningGuard</h1>
<p class="mg-sub">Checks that an English-to-Hindi translation keeps every number,
negation and condition.</p>
<div class="mg-stages"><span>lexer</span><span>parser</span><span>verifier</span><span>repair</span>
<span>tone</span></div></div>
<div class="mg-sample"><b>error[NUMBER_CHANGED]</b>: ₹500 became ₹550
  | शुक्रवार तक ₹५५० का भुगतान करें।
  |            <b>^^^</b>  expected <i>₹५००</i></div></div>"""


@st.cache_resource(show_spinner=False)
def get_pipeline(google: bool) -> mg.Pipeline:
    """One pipeline per process. The models load on first use, so building it is instant."""
    cfg = load_config()
    runtime = dataclasses.replace(cfg.runtime, google_fallback=google)
    pipeline = mg.build_pipeline(dataclasses.replace(cfg, runtime=runtime), tone=True)
    pipeline.lock = (
        threading.Lock()
    )  # one model call at a time: the models are shared by every visitor
    return pipeline


def use_example(index: int) -> None:
    _, english, hindi = EXAMPLES[index]
    st.session_state.update(source=english, own=hindi is not None, translation=hindi or "")
    st.session_state.pop("outcome", None)


def sidebar_cache(cache) -> None:
    if cache is None:
        st.caption("The cache is off.")
        return
    if st.button("Clear cache", help="Forget saved translations and tone scores."):
        cache.clear()
    st.caption(f"{cache.stats()['total']} saved model results")


def run_check(source: str, translation: str | None, repair: bool, tone: bool, google: bool):
    base = get_pipeline(google)
    pipe = mg.Pipeline(
        base.chain, do_repair=repair, tone=base.tone if tone else None, cache=base.cache
    )
    try:
        with (
            st.spinner("Checking. The first run loads the models and can take a minute."),
            base.lock,
        ):
            return {"result": pipe.check(source, translation)}
    except LexError as exc:
        return {"warning": f"Cannot read the input: {exc}"}
    except mg.TranslationFailed as exc:
        lines = "\n".join(f"- {e}" for e in exc.errors)
        hint = "Paste your own translation to still run the check."
        return {"error": f"No translator produced Hindi.\n{lines}\n\n{hint}"}


def html(markup: str, target=st) -> None:
    target.markdown(markup, unsafe_allow_html=True)


def show_facts(result) -> None:
    report = result.repair.after
    src_marks, tgt_marks = render.finding_marks(report)
    left, right = st.columns(2)
    html(render.sheet_html("English", result.source_ir.text, src_marks), left)
    label = "Hindi after repair" if result.repair.repaired else "Hindi"
    html(render.sheet_html(label, result.final, tgt_marks, "hi"), right)
    right.code(result.final, language=None)
    st.subheader("Findings")
    cards = render.finding_cards(report)
    if not cards:
        ok = "Numbers, dates, negation and conditions agree with the source."
        html(f'<div class="mg-clean">{ok}</div>')
    for c in cards:
        html(render.card_html(c))


def show_repair(result) -> None:
    repair = result.repair
    for r in repair.reverted:
        st.info(f"A fix was discarded because it was not safe: {r['message']}")
    if not repair.repaired:
        return
    st.subheader("Repair")
    before, marks_before, marks_after = render.edit_views(result.final, repair.edits)
    a, b = st.columns(2)
    html(render.sheet_html("Before", before, marks_before, "hi"), a)
    html(render.sheet_html("After", result.final, marks_after, "hi"), b)
    st.table(
        [
            {"Fixes": code, "Was": old, "Now": new}
            for code, old, new in render.repair_summary(repair)
        ]
    )
    st.caption(
        "Only the wrong number or currency was changed; the rest of the translation is untouched."
    )


def show_tone(result) -> None:
    st.subheader("Emotion and tone")
    data = render.tone_rows(result.tone)
    if data is None:
        st.caption("The tone check is off (sidebar).")
        return
    st.caption(
        "Advisory: models read the emotion of the English and of the Hindi (through its English "
        "back-translation); this never changes the Hindi text."
    )
    for column, (name, side) in zip(st.columns(2), data["sides"], strict=True):
        html(render.tone_side_html(name, side), column)
        if side:
            with column.expander("model signals"):
                st.table(
                    [
                        {"model": n, "label": lab, "probability": f"{p:.2f}"}
                        for n, lab, p in side["signals"]
                    ]
                )
    for c in data["findings"]:
        html(render.card_html(c))
    for note in data["notes"]:
        st.caption(note)


def show(result) -> None:
    verdict = render.verdict(result.report)
    html(render.verdict_html(verdict, len(result.repair.edits)))
    if result.cache["hits"] and not result.cache["misses"]:
        st.caption("Answered from the cache: no model had to run.")
    show_facts(result)
    show_repair(result)
    show_tone(result)
    with st.expander("Details"):
        st.write(f"Translator: `{result.translator}`")
        for note in result.notes:
            st.caption(note)
        st.write(f"Cache: {result.cache['hits']} reused, {result.cache['misses']} computed")
        st.json(result.timings)
        payload = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
        st.download_button(
            "Download result (JSON)", payload, "meaningguard_result.json", "application/json"
        )
        st.json(result.source_ir.to_dict(), expanded=False)
        st.json(result.target_ir.to_dict(), expanded=False)


def main() -> None:
    st.set_page_config(page_title="MeaningGuard", layout="wide", initial_sidebar_state="collapsed")
    html(f"<style>{THEME.read_text('utf-8')}</style>")
    html(HERO)
    with st.sidebar:
        st.header("Options")
        repair = st.toggle("Repair wrong numbers and currencies", True)
        tone = st.toggle(
            "Emotion, tone and sarcasm check",
            TONE_DEFAULT,
            help="About 4 s more per sentence on CPU.",
        )
        google = st.toggle("Google fallback", False, help="Unofficial and rate limited.")
        sidebar_cache(get_pipeline(google).cache)
        st.caption("Models translate and read emotion; verification and repair are rule-based.")

    st.write("Try an example")
    for column, (i, (label, _, _)) in zip(
        st.columns(len(EXAMPLES)), enumerate(EXAMPLES), strict=True
    ):
        column.button(
            label, key=f"example{i}", on_click=use_example, args=(i,), use_container_width=True
        )
    source = st.text_area("English sentence", key="source", height=90)
    own = st.checkbox("I already have a Hindi translation", key="own")
    translation = st.text_area("Hindi translation", key="translation", height=90) if own else None
    if st.button("Check", type="primary"):
        if own and not (translation or "").strip():
            st.session_state["outcome"] = {
                "warning": "Paste the Hindi translation, or untick the box."
            }
        else:
            st.session_state["outcome"] = run_check(
                source, translation if own else None, repair, tone, google
            )
    outcome = st.session_state.get("outcome")
    if outcome:
        for kind in ("warning", "error"):
            if kind in outcome:
                getattr(st, kind)(outcome[kind])
        if "result" in outcome:
            show(outcome["result"])


main()
