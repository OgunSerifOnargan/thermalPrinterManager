"""Integration: idempotency_key dedupes parallel/repeat submissions."""
from __future__ import annotations

import asyncio

from httpx import AsyncClient

from app.services.job_repository import JobRepository


def base_body(key: str) -> dict:
    return {
        "machine_id": "ACO-IDEMP",
        "items": [{"product": "Glass", "quantity": 1, "reward": 1.0}],
        "total_reward": 1.0,
        "timestamp": "2026-05-27T22:00:00Z",
        "idempotency_key": key,
    }


async def test_same_key_twice_returns_one_job(client: AsyncClient,
                                              repository: JobRepository):
    await client.post("/connect", json={"mode": "lan"})
    body = base_body("k-1")
    r1 = await client.post("/print/text", json=body)
    r2 = await client.post("/print/text", json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    j1 = r1.json()
    j2 = r2.json()
    assert j1["job_id"] == j2["job_id"]
    assert j2["idempotent_replay"] is True
    # Repository has exactly one row for this key
    rec = repository.get_by_idempotency_key("k-1")
    assert rec is not None


async def test_different_keys_create_distinct_jobs(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    r1 = await client.post("/print/text", json=base_body("a"))
    r2 = await client.post("/print/text", json=base_body("b"))
    assert r1.json()["job_id"] != r2.json()["job_id"]


async def test_no_key_does_not_dedupe(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    body = base_body("ignored")
    body.pop("idempotency_key")
    r1 = await client.post("/print/text", json=body)
    r2 = await client.post("/print/text", json=body)
    assert r1.json()["job_id"] != r2.json()["job_id"]
