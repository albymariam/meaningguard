---
title: MeaningGuard
emoji: 🛡️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8501
pinned: false
short_description: Checks that an English to Hindi translation keeps its numbers, negation and conditions.
---

# MeaningGuard

Paste an English sentence. The app translates it to Hindi with IndicTrans2, compiles both sides to a
typed representation, and compares facts: numbers, currencies, dates, negation, conditions.
A wrong number or currency is fixed with a minimal edit. Tone and sarcasm checks are optional
(sidebar) and are advisory only.

- Parsing, verification and repair are rule-based; only translation and emotion use pretrained models.
- Runs on free CPU: the first request after a sleep takes about a minute.
- Hindi text you paste yourself is checked without any translation.
