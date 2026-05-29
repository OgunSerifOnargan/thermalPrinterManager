"""Shared pytest fixtures. Grows per phase."""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from app.core.config import reset_settings_for_test


@pytest.fixture(autouse=True)
def _clean_settings() -> Iterator[None]:
    """Reset settings singleton between tests so env vars are re-read."""
    reset_settings_for_test()
    yield
    reset_settings_for_test()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Convenience: monkeypatch env vars, auto-resets settings."""
    reset_settings_for_test()
    yield monkeypatch
    reset_settings_for_test()
