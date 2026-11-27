"""Shared test setup.

`doctor` keeps the conversions it makes only to compare, so a test that checks
a drifted profile writes cache entries. Pointing the cache at the test's own
temporary directory keeps the developer's real cache untouched and stops one
test from answering another's cache lookup.
"""

from __future__ import annotations

import pytest

from skill_bridge.cache import CACHE_ENV


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv(CACHE_ENV, str(tmp_path / "cache"))
