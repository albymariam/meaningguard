"""Fingerprints (spec 02 section 6).

``fp(node)`` is the first 16 hex characters of SHA-256 over the canonical JSON of
``{"t": node type, "a": canonical attributes, "c": child fingerprints}``. Surface text, spans and
flags never enter it. Two nodes are equal if and only if their fingerprints match.
"""

from __future__ import annotations

import hashlib
import json

from .nodes import Fact, Value


def _dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def node_type(node) -> str:
    return node.kind if isinstance(node, Fact) else node.type


def canon_tree(node) -> dict:
    """The structure that is hashed: type, canonical attributes, child fingerprints."""
    attrs, children = node.canon_parts()
    if isinstance(node, Value):
        attrs = {k: v for k, v in attrs.items() if k != "type"}
    return {"t": node_type(node), "a": attrs, "c": {k: fp(c) for k, c in sorted(children.items())}}


def digest(tree: dict) -> str:
    return hashlib.sha256(_dumps(tree).encode("utf-8")).hexdigest()[:16]


def fp(node) -> str:
    return digest(canon_tree(node))


def canon_full(node) -> dict:
    """Like ``canon_tree`` but with the children inlined, so sibling order is readable
    (500 before 600) instead of following hash order."""
    attrs, children = node.canon_parts()
    if isinstance(node, Value):
        attrs = {k: v for k, v in attrs.items() if k != "type"}
    return {
        "t": node_type(node),
        "a": attrs,
        "c": {k: canon_full(c) for k, c in sorted(children.items())},
    }


def order_key(fact) -> tuple:
    """C-ORDER sort key: node type first, then canonical JSON."""
    from .nodes import KIND_ORDER

    return (KIND_ORDER.index(fact.kind), _dumps(canon_full(fact)))


def clause_fp(clause) -> str:
    """Clause-scope negations are carried by ``polarity`` (so 'unless you pay' and 'if you do not
    pay' agree), not by a fact; only their strength is added to the attributes."""
    from .nodes import Neg

    cond = [clause.cond.op, clause.cond.role] if clause.cond else None
    clause_negs = [f for f in clause.facts if isinstance(f, Neg) and f.scope == "CLAUSE"]
    tree = {
        "t": "CLAUSE",
        "a": {
            "polarity": clause.polarity,
            "cond": cond,
            "weak": any(f.strength == "WEAK" for f in clause_negs),
        },
        "c": {"facts": [fp(f) for f in clause.facts if f not in clause_negs]},
    }
    return digest(tree)


def sentence_fp(clause_fps: list) -> str:
    """Clause order does not change the fingerprint: conditional roles are inside each clause."""
    return digest({"t": "SENTENCE", "a": {}, "c": {"clauses": sorted(clause_fps)}})
