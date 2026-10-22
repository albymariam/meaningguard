"""Tests never touch the on-disk cache unless they build their own ``Cache``."""

import pytest


@pytest.fixture(autouse=True)
def no_disk_cache(monkeypatch):
    monkeypatch.setenv("MEANINGGUARD_CACHE", "off")
