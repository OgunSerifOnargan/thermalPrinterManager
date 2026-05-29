"""G5: per-IP rate limit on POST /print/* configurable via env."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.rate_limit import reset_rate_limit_buckets


def _print_body() -> dict:
    return {
        "machine_id": "M",
        "items": [{"product": "x", "quantity": 1, "reward": 1.0}],
        "total_reward": 1.0,
        "timestamp": "2026-05-28T10:00:00Z",
    }


@pytest.fixture(autouse=True)
def _reset_buckets():
    reset_rate_limit_buckets()
    yield
    reset_rate_limit_buckets()


async def test_429_after_configured_limit(client: AsyncClient, settings):
    settings.print_rate_limit_per_min = 3
    await client.post("/connect", json={"mode": "lan"})

    for i in range(3):
        r = await client.post("/print/text", json=_print_body())
        assert r.status_code == 200, f"req #{i} should succeed"

    r4 = await client.post("/print/text", json=_print_body())
    assert r4.status_code == 429
    body = r4.json()["detail"]
    assert body["error_code"] == "RATE_LIMITED"
    assert body["retry_after_s"] >= 1
    assert "Retry-After" in r4.headers


async def test_zero_disables_limit(client: AsyncClient, settings):
    settings.print_rate_limit_per_min = 0
    await client.post("/connect", json={"mode": "lan"})
    # 5 quick prints; none should rate-limit.
    for _ in range(5):
        r = await client.post("/print/text", json=_print_body())
        assert r.status_code == 200


async def test_limit_per_ip_via_x_forwarded_for(client: AsyncClient, settings):
    """Different X-Forwarded-For values get their own buckets."""
    settings.print_rate_limit_per_min = 2
    await client.post("/connect", json={"mode": "lan"})

    headers_a = {"x-forwarded-for": "10.0.0.1"}
    headers_b = {"x-forwarded-for": "10.0.0.2"}

    assert (await client.post("/print/text", json=_print_body(), headers=headers_a)).status_code == 200
    assert (await client.post("/print/text", json=_print_body(), headers=headers_a)).status_code == 200
    # IP-A exhausted
    assert (await client.post("/print/text", json=_print_body(), headers=headers_a)).status_code == 429
    # IP-B still has budget
    assert (await client.post("/print/text", json=_print_body(), headers=headers_b)).status_code == 200
