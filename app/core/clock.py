"""Injectable time provider.

Production uses SystemClock. Tests use FakeClock to fast-forward through
OVERHEAT recovery deterministically (no real waiting).
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """Test clock — manual tick control."""

    def __init__(self, start: float = 0.0):
        self._mono = start
        self._wall = datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def tick(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot rewind time")
        self._mono += seconds
        # advance wall time too so timestamps stay coherent
        from datetime import timedelta
        self._wall = self._wall + timedelta(seconds=seconds)
