"""Persistent cache for the slow model calls: translations and classifier scores.

Only model outputs are cached, never verdicts: compiling, verifying and repairing are fast and
deterministic, so a cached translation is verified again every time, and a change to the rules, to
the thresholds in ``config.yaml`` or to the repair takes effect at once. A key holds everything that
changes a model's output (model, pinned commit, language pair, generation settings, text). Pin the
models (``tools/pin_models.py --write``): an unpinned model can change without the key noticing.

The cache is best effort. A damaged or locked database is a miss, never an error.

    python -m meaningguard.cache stats
    python -m meaningguard.cache clear
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Config, load_config

ROOT = Path(__file__).resolve().parent.parent
OFF = ("0", "off", "false", "no")


def make_key(parts) -> str:
    text = json.dumps(list(parts), ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Cache:
    """A small key-value store in SQLite, safe to share between threads."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.hits = self.misses = self.errors = 0
        self._lock = threading.Lock()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS kv "
                "(ns TEXT, key TEXT, value TEXT, PRIMARY KEY (ns, key))"
            )
            self._db.commit()

    def get(self, ns: str, parts):
        """The cached value, or ``None``."""
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT value FROM kv WHERE ns=? AND key=?", (ns, make_key(parts))
                ).fetchone()
            value = json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError):
            self.errors += 1
            value = None
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def set(self, ns: str, parts, value) -> None:
        try:
            with self._lock:
                self._db.execute(
                    "INSERT OR REPLACE INTO kv (ns, key, value) VALUES (?, ?, ?)",
                    (ns, make_key(parts), json.dumps(value, ensure_ascii=False)),
                )
                self._db.commit()
        except (sqlite3.Error, TypeError, ValueError):
            self.errors += 1

    def clear(self, ns: str | None = None) -> int:
        """Delete everything (or one namespace); returns how many entries were removed."""
        with self._lock:
            if ns is None:
                cur = self._db.execute("DELETE FROM kv")
            else:
                cur = self._db.execute("DELETE FROM kv WHERE ns=?", (ns,))
            self._db.commit()
            return cur.rowcount

    def stats(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT ns, COUNT(*) FROM kv GROUP BY ns").fetchall()
        size = (
            os.path.getsize(self.path)
            if self.path != ":memory:" and os.path.exists(self.path)
            else 0
        )
        return {
            "path": self.path,
            "entries": {ns: n for ns, n in rows},
            "total": sum(n for _, n in rows),
            "bytes": size,
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
        }

    def close(self) -> None:
        with self._lock:
            self._db.close()


# ---------------------------------------------------------------------------------------------
# Wrappers: same interface as the object they wrap, so nothing else needs to know


class _Wrapper:
    def __init__(self, inner, cache: Cache):
        self.inner, self.cache = inner, cache

    def __getattr__(self, name):  # model_id, revision, name ... come from the wrapped object
        return getattr(self.inner, name)

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close:
            close()


class CachedTranslator(_Wrapper):
    """``translate(list[str]) -> list[str]``; only the sentences not seen before reach the model."""

    def translate(self, sentences: list) -> list:
        out: list = [None] * len(sentences)
        missing: dict = {}
        for i, sentence in enumerate(sentences):
            hit = self.cache.get("translate", [*self.inner.cache_key, sentence])
            if hit is None:
                missing.setdefault(sentence, []).append(i)
            else:
                out[i] = hit
        if missing:
            fresh = self.inner.translate(list(missing))
            if len(fresh) != len(missing):
                raise RuntimeError("the translator returned a different number of sentences")
            for (sentence, slots), text in zip(missing.items(), fresh, strict=True):
                if isinstance(text, str) and text.strip():
                    self.cache.set("translate", [*self.inner.cache_key, sentence], text)
                for i in slots:
                    out[i] = text
        return out


class CachedClassifier(_Wrapper):
    """``classify(text) -> {label: probability}``."""

    def classify(self, text: str) -> dict:
        parts = [*self.inner.cache_key, text]
        hit = self.cache.get("classify", parts)
        if hit is not None:
            return hit
        scores = self.inner.classify(text)
        self.cache.set("classify", parts, scores)
        return scores


# ---------------------------------------------------------------------------------------------
# Setup


def cache_path(cfg: Config) -> Path:
    path = Path(cfg.cache.path)
    return path if path.is_absolute() else ROOT / path


def open_cache(cfg: Config) -> Cache | None:
    """The cache from ``cfg.cache``, or None when it is off (``enabled: false`` or the
    ``MEANINGGUARD_CACHE=off`` environment variable). A cache that cannot be opened is off too."""
    if not cfg.cache.enabled or os.environ.get("MEANINGGUARD_CACHE", "").lower() in OFF:
        return None
    try:
        return Cache(cache_path(cfg))
    except (OSError, sqlite3.Error):
        return None


@dataclass
class Tally:
    """Hits and misses of a cache since it was created; ``delta`` measures one stretch of work."""

    cache: Cache | None
    hits: int = 0
    misses: int = 0

    def start(self) -> None:
        if self.cache:
            self.hits, self.misses = self.cache.hits, self.cache.misses

    def delta(self) -> dict:
        if not self.cache:
            return {"hits": 0, "misses": 0}
        return {"hits": self.cache.hits - self.hits, "misses": self.cache.misses - self.misses}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in ("stats", "clear"):
        print("usage: python -m meaningguard.cache stats | clear")
        return 2
    cfg = load_config()
    path = cache_path(cfg)
    if not path.exists():
        print(f"no cache yet ({path})")
        return 0
    cache = Cache(path)
    if args[0] == "clear":
        print(f"removed {cache.clear()} entries")
    else:
        print(json.dumps(cache.stats(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
