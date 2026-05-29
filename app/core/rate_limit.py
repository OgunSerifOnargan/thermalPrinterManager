"""G5: per-IP rate limiting on the print endpoints.

The limit is read from settings at *call* time (not at decorator time), so
PATCH /config can adjust it without a restart. When the setting is 0, the
dependency becomes a no-op.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.core.config import get_settings


log = logging.getLogger(__name__)

# Per-IP sliding window of request timestamps (monotonic seconds).
_BUCKETS: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=2000))


def _client_key(request: Request) -> str:
    # Prefer X-Forwarded-For (if running behind a proxy), fall back to peer IP.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",", 1)[0].strip()
    if request.client is None:
        return "unknown"
    return request.client.host


async def print_rate_limit(request: Request) -> None:
    settings = get_settings()
    limit = settings.print_rate_limit_per_min
    if limit <= 0:
        return  # disabled

    key = _client_key(request)
    now = time.monotonic()
    window_start = now - 60.0

    bucket = _BUCKETS[key]
    # Discard old timestamps lazily — keeps the bucket bounded.
    while bucket and bucket[0] < window_start:
        bucket.popleft()

    if len(bucket) >= limit:
        retry_after = max(1, int(60.0 - (now - bucket[0])))
        log.warning(
            "print rate limit hit",
            extra={
                "op": "rate_limit", "client": key,
                "limit_per_min": limit, "retry_after_s": retry_after,
            },
        )
        raise HTTPException(
            status_code=429,
            detail={
                "ok": False,
                "error_code": "RATE_LIMITED",
                "detail": f"Too many print requests; limit is {limit}/min per client.",
                "retry_after_s": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


def reset_rate_limit_buckets() -> None:
    """Test helper — clears all per-IP state."""
    _BUCKETS.clear()
