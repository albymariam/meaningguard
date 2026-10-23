"""What the app shows, as plain data and HTML strings (no Streamlit, so it is testable).

Marks are ``(start, end, severity)`` spans over the normalized text of an IR; when marks overlap,
the more severe one wins for the overlapping characters.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from .hints import hint
from .verifier import describe_diff

RANK = {"ok": 1, "info": 2, "warning": 3, "error": 4}  # "ok" marks a repaired spot
STATUS_TEXT = {
    "pass": "Meaning preserved",
    "warn": "Check these points",
    "fail": "Meaning may have changed",
}


# ---------------------------------------------------------------------------------------------
# Highlighted text


def to_html(text: str, marks: list) -> str:
    """``text`` as escaped HTML with ``<mark class="mg-<severity>">`` runs."""
    rank = [0] * len(text)
    names = {v: k for k, v in RANK.items()}
    for start, end, severity in marks:
        for i in range(max(start, 0), min(end, len(text))):
            rank[i] = max(rank[i], RANK.get(severity, 1))
    out: list = []
    i = 0
    while i < len(text):
        j = i
        while j < len(text) and rank[j] == rank[i]:
            j += 1
        chunk = escape(text[i:j])
        if rank[i]:
            out.append(f'<mark class="mg-{names[rank[i]]}">{chunk}</mark>')
        else:
            out.append(chunk)
        i = j
    return "".join(out)


def finding_marks(report) -> tuple:
    """Marks for the source text and the target text from the spans of the findings."""
    source, target = [], []
    for f in report.findings:
        if f.source and f.source.get("span"):
            source.append((*f.source["span"], f.severity))
        if f.target and f.target.get("span"):
            target.append((*f.target["span"], f.severity))
    return source, target


def edit_views(final: str, edits: list) -> tuple:
    """``(text before repair, marks on it, marks on the final text)`` from the applied edits.

    Edits carry offsets into the text before repair; the final text is that text with every edit
    applied, so each edit moves by the length change of the edits before it."""
    before, marks_before, marks_after = final, [], []
    shift = 0
    placed = []
    for e in sorted(edits, key=lambda e: e.start):
        new_start = e.start + shift
        placed.append((new_start, new_start + len(e.new), e.old))
        marks_before.append((e.start, e.end, "error"))
        marks_after.append((new_start, new_start + len(e.new), "ok"))
        shift += len(e.new) - len(e.old)
    for start, end, old in reversed(placed):
        before = before[:start] + old + before[end:]
    return before, marks_before, marks_after


# ---------------------------------------------------------------------------------------------
# Verdict and findings


@dataclass
class Verdict:
    status: str  # pass, warn, fail
    title: str
    note: str  # why the check is limited, or ""
    basic: bool  # tier 3: only numbers and negation were compared


def verdict(report) -> Verdict:
    note, basic = "", False
    if report.confidence == "low":
        basic = True
        note = (
            "Basic check: the sentence could not be fully parsed, "
            "so only numbers and negation were compared."
        )
    elif report.confidence == "medium":
        note = (
            "Partly parsed: a fact the parser could not read is reported as "
            "unverified, not as an error."
        )
    return Verdict(report.status, STATUS_TEXT[report.status], note, basic)


@dataclass
class Card:
    code: str
    severity: str
    message: str
    rows: list  # (what, source value, translation value)
    confidence: str
    hint: str | None = None  # what to change, in plain words


def finding_cards(report) -> list:
    cards = []
    for f in sorted(report.findings, key=lambda f: -RANK[f.severity]):
        rows = []
        for d in f.diffs:
            src, tgt = describe_diff(d)
            rows.append((d.path, src, tgt))
        cards.append(Card(f.code, f.severity, f.message, rows, f.confidence, hint(f)))
    return cards


# ---------------------------------------------------------------------------------------------
# Tone


def tone_rows(tone) -> dict | None:
    """Tone data for the panel, or None when the check was off."""
    if tone is None:
        return None
    sides = []
    for name, rec in (("English source", tone.source), ("Hindi translation", tone.target)):
        if rec is None:
            sides.append((name, None))
            continue
        sides.append(
            (
                name,
                {
                    "sentiment": rec["sentiment"],
                    "emotion": rec["label"],
                    "emotions": sorted(
                        (rec.get("emotions") or {}).items(), key=lambda kv: kv[1], reverse=True
                    ),
                    "confidence": rec["confidence"],
                    "sarcasm": rec["possible_sarcasm"],
                    "signals": [(s["name"], s["label"], s["prob"]) for s in rec["signals"]],
                },
            )
        )
    return {"sides": sides, "notes": tone.notes, "findings": finding_cards_of(tone.findings)}


def finding_cards_of(findings: list) -> list:
    return [Card(f.code, f.severity, f.message, [], f.confidence) for f in findings]


def repair_summary(repair) -> list:
    """One line per applied edit: ``(code, old, new)``."""
    edits = sorted(repair.edits, key=lambda e: e.start)
    return [(e.code, e.old or "(nothing)", e.new) for e in edits]


# ---------------------------------------------------------------------------------------------
# HTML pieces (styled by theme.css)

GLYPH = {"pass": "✓", "warn": "!", "fail": "✕"}


def verdict_html(v: Verdict, fixed: int = 0) -> str:
    lines = [v.note] if v.note else []
    if fixed:
        lines.append(f"{fixed} wrong value{'s' if fixed > 1 else ''} fixed automatically.")
    sub = f'<div class="mg-vs">{escape(" ".join(lines))}</div>' if lines else ""
    return (
        f'<div class="mg-verdict mg-status-{v.status}">'
        f'<div class="mg-glyph">{GLYPH[v.status]}</div>'
        f'<div><div class="mg-vt">{escape(v.title)}</div>{sub}</div></div>'
    )


def sheet_html(label: str, text: str, marks: list, lang: str = "en") -> str:
    return (
        f'<div class="mg-sheet"><div class="mg-tab">{escape(label)}</div>'
        f'<div class="mg-text" lang="{lang}">{to_html(text, marks)}</div></div>'
    )


def card_html(c: Card) -> str:
    """A finding drawn like a compiler diagnostic."""
    pairs = "".join(
        f"<div><small>English</small><b>{escape(str(src))}</b></div>"
        f'<div class="mg-bad"><small>Hindi</small><b>{escape(str(tgt))}</b></div>'
        for _, src, tgt in c.rows
    )
    pairs = f'<div class="mg-pair">{pairs}</div>' if pairs else ""
    fix = f'<p class="mg-hint"><b>Suggested fix:</b> {escape(c.hint)}</p>' if c.hint else ""
    return (
        f'<div class="mg-diag mg-sev-{c.severity}"><div class="mg-diag-head">'
        f"{c.severity}[{escape(c.code)}]</div><p>{escape(c.message)}</p>{pairs}{fix}</div>"
    )


def tone_side_html(name: str, side: dict | None) -> str:
    if side is None:
        head = f'<div class="mg-tone-name">{escape(name)}</div>'
        return f'<div class="mg-tone">{head}not available</div>'
    sentiment = escape(side["sentiment"])
    emotions = side.get("emotions") or []
    label = side["emotion"]
    if label == "uncertain" and emotions:
        label = f"{emotions[0][0]} (low confidence)"
    chips = [
        f'<span class="mg-chip mg-emotion">emotion: {escape(label)}</span>',
        f'<span class="mg-chip mg-s-{sentiment}">sentiment: {sentiment}</span>',
    ]
    bars = "".join(
        f'<div class="mg-bar"><span class="mg-bar-name">{escape(name)}</span>'
        f'<span class="mg-bar-track"><i style="width:{round(p * 100)}%"></i></span>'
        f'<span class="mg-bar-pct">{round(p * 100)}%</span></div>'
        for name, p in emotions
    )
    if side["sarcasm"]:
        chips.append('<span class="mg-chip mg-sarcasm">possible sarcasm</span>')
    pct = round(side["confidence"] * 100)
    return (
        f'<div class="mg-tone"><div class="mg-tone-name">{escape(name)}</div>'
        f'<div class="mg-chips">{"".join(chips)}</div>'
        f'<div class="mg-bars">{bars}</div>'
        f'<div class="mg-meter"><span style="width:{pct}%"></span></div>'
        f'<div class="mg-meter-label">confidence {pct}%</div></div>'
    )
