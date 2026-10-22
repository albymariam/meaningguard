# 08 Emotion and sarcasm (v1)

Advisory only. Pretrained models, no fine-tuning; everything around them is deterministic. The result
fills the `emotion` field of the IR (05 section 6) and feeds the tone findings (06 section 4).

## 1. Models (versions pinned in `config.yaml`, `tools/pin_models.py`)
| Name | Model | Reads | Labels |
|---|---|---|---|
| sentiment | `cardiffnlp/twitter-xlm-roberta-base-sentiment` | English and Hindi | negative, neutral, positive |
| emotion | `j-hartmann/emotion-english-distilroberta-base` | English only | anger, disgust, fear, joy, neutral, sadness, surprise |
| irony | `cardiffnlp/twitter-roberta-base-irony` | English source only | non_irony, irony |

## 2. Signals
- Source: sentiment on the text, emotion on the text, irony on the text.
- Translation: sentiment on the Hindi text, then sentiment and emotion on its English
  back-translation (indic-en model). Without a back-translation only one signal exists, so the
  result is `uncertain`: this is deliberate, one model is never enough.
- An emotion maps to a sentiment: anger, disgust, fear, sadness are negative; joy positive; neutral
  neutral; surprise abstains.

## 3. Voting (`min_prob` 0.4)
A signal with a stated polarity and probability >= `min_prob` is usable. The sentiment is the
polarity backed by at least two usable signals (ties go to the higher summed probability), else
`uncertain`. `confidence` = summed probability of the agreeing signals / number of signals that
state a polarity, so a dissenting signal lowers it. `votes` is the number of agreeing signals.
The emotion `label` is the emotion model's top label if its probability >= `label_prob` (0.5), else
`uncertain`.

## 4. Sarcasm (source only, `possible_sarcasm`)
False when the sentiment and the emotion model both read the text as neutral (no stance, nothing to
be sarcastic about). Otherwise true when the irony probability >= `irony_prob` (0.9), or >= `irony_low`
(0.35) while the sentiment model reads positive and the emotion model reads negative, both usable.
The irony model scores 0.98 on both "I just love it when my phone dies" and "I really love it when my
phone battery lasts all day", so a sincere sentence can still be flagged; this is why it is only a
warning. On the 28 dev rows (unreviewed labels) the irony-only cut at 0.5 gave precision 0.44, recall
1.0; every true sarcastic row scored >= 0.98, so 0.9 loses nothing there. The cut is re-checked
against human labels at Phase 9. The translation is not judged for sarcasm (`null`); its loss is
inferred from its sentiment (06 section 4).

## 5. Limits
- Sarcasm detection is the weakest part (small English-only model); it is reported as "possible".
- The Hindi side relies on the English back-translation, which adds its own errors.
- Texts are cut at `max_length` tokens (128). An empty text gives an `uncertain` record.
- Thresholds are tuned on the dev split only (`tools/eval_emotion.py`); the held-out split is used
  once, at the final evaluation.
- Tone never changes the translation and never produces an error.
