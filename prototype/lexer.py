"""Stage 1: lexical analysis for English and Hindi text."""
import re
import unicodedata
from collections import namedtuple

from ir import Quantity, Negation

Token = namedtuple("Token", "type text pos")


def _n(s):
    return unicodedata.normalize("NFC", s)


# Symbol tables (lexicons)
NEGATIONS = {_n(w) for w in [
    "not", "no", "never", "none", "nothing", "nobody", "neither", "nor",
    "cannot", "don't", "doesn't", "didn't", "won't", "can't", "isn't",
    "aren't", "wasn't", "weren't", "shouldn't", "wouldn't", "couldn't",
    "नहीं", "नही", "न", "मत", "कभी",
]}
CURRENCY_WORDS = {_n(w) for w in ["rs", "inr", "rupee", "rupees", "रुपये", "रुपए", "रुपया", "रू"]}
MULTIPLIERS = {_n(k): v for k, v in {
    "thousand": 1e3, "k": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5,
    "crore": 1e7, "crores": 1e7, "million": 1e6, "billion": 1e9,
    "हज़ार": 1e3, "हजार": 1e3, "लाख": 1e5, "करोड़": 1e7, "करोड": 1e7,
    "मिलियन": 1e6,
}.items()}

TOKEN_RE = re.compile(
    r"(?P<NUMBER>[0-9\u0966-\u096F]+(?:[,.][0-9\u0966-\u096F]+)*)"
    r"|(?P<CURSYM>₹)"
    r"|(?P<WORD>[A-Za-z\u0900-\u0965\u0970-\u097F'’]+)"
    r"|(?P<PUNCT>[^\s\w])"
)


def _to_float(s):
    s = s.replace(",", "")
    s = "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in s)
    return float(s)


def tokenize(text):
    text = _n(text)
    tokens = []
    for m in TOKEN_RE.finditer(text):
        kind, val = m.lastgroup, m.group()
        low = val.lower().replace("’", "'")
        if kind == "WORD":
            if low in NEGATIONS:
                kind = "NEG"
            elif low in CURRENCY_WORDS:
                kind = "CURRENCY"
            elif low in MULTIPLIERS:
                kind = "MULT"
        elif kind == "CURSYM":
            kind = "CURRENCY"
        tokens.append(Token(kind, val, m.start()))
    return tokens


def extract_quantities(tokens):
    out = []
    for i, t in enumerate(tokens):
        if t.type != "NUMBER":
            continue
        value, raw, cur = _to_float(t.text), t.text, None
        j = i + 1
        if j < len(tokens) and tokens[j].type == "MULT":
            value *= MULTIPLIERS[_n(tokens[j].text.lower())]
            raw += " " + tokens[j].text
            j += 1
        k = i - 1
        if k > 0 and tokens[k].text == "." and tokens[k - 1].type == "CURRENCY":
            k -= 1  # "Rs. 5" -> skip the dot after the currency word
        if k >= 0 and tokens[k].type == "CURRENCY":
            cur, raw = "INR", tokens[k].text + raw
        elif j < len(tokens) and tokens[j].type == "CURRENCY":
            cur, raw = "INR", raw + " " + tokens[j].text
        out.append(Quantity(raw=raw, value=value, currency=cur, pos=i))
    return out


def extract_negations(tokens):
    return [Negation(raw=t.text, pos=i) for i, t in enumerate(tokens) if t.type == "NEG"]


if __name__ == "__main__":
    for s in ["The price fell from ₹500 to ₹400.",
              "Rs. 5 lakh left, do not pay.",
              "कीमत ₹५ लाख है, भुगतान मत करो।"]:
        toks = tokenize(s)
        print(s)
        print("  Q:", [(q.raw, q.value, q.currency) for q in extract_quantities(toks)])
        print("  N:", [n.raw for n in extract_negations(toks)])
