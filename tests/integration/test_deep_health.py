"""G6: /healthz reports each subsystem and returns 503 if any is degraded."""
from __future__ import annotations

from httpx import AsyncClient


async def test_healthz_all_green_when_idle(client: AsyncClient):
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # All three subsystems should report individually.
    for k in ("database", "reconcile_loop", "printer_link"):
        assert k in body["checks"]
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["reconcile_loop"]["ok"] is True
    # No connect yet → no target_mode → printer_link is OK with a note.
    assert body["checks"]["printer_link"]["ok"] is True


async def test_healthz_503_if_reconcile_loop_dead(client: AsyncClient, loop):
    # Kill the background loop and verify /healthz drops to 503.
    await loop.stop()
    r = await client.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["checks"]["reconcile_loop"]["ok"] is False


async def test_healthz_connected_state_is_healthy(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    r = await client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["checks"]["printer_link"]["ok"] is True
    assert body["checks"]["printer_link"]["connected"] is True
