"""Integration: /connect, /status, /disconnect + reconcile loop behavior."""
from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.core.errors import CommError
from app.core.states import DisconnectReason, PrinterState
from app.devices.mock_printer import MockPrinter
from app.services.connection_manager import ConnectionManager


async def wait_for(predicate, timeout: float = 1.0, interval: float = 0.01) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"timeout waiting for condition (>{timeout}s)")


async def test_health_endpoint(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "aco-thermal-printer-service"


async def test_connect_then_status_shows_idle(client: AsyncClient,
                                              manager: ConnectionManager):
    r = await client.post("/connect", json={"mode": "lan"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == PrinterState.IDLE.value

    s = await client.get("/status")
    body = s.json()
    assert body["printer_state"] == PrinterState.IDLE.value
    assert body["connection"]["connected"] is True
    assert body["connection"]["mode"] == "lan"
    assert body["device"]["paper"] == "ok"
    assert body["device"]["cover"] == "closed"


async def test_disconnect_clears_target_mode(client: AsyncClient,
                                             manager: ConnectionManager):
    await client.post("/connect", json={"mode": "lan"})
    assert manager.target_mode == "lan"
    r = await client.post("/disconnect")
    assert r.status_code == 200
    assert manager.target_mode is None
    assert manager.disconnect_reason == DisconnectReason.USER_REQUESTED


async def test_user_disconnect_does_not_auto_reconnect(client: AsyncClient,
                                                       manager: ConnectionManager):
    await client.post("/connect", json={"mode": "lan"})
    await client.post("/disconnect")
    # Wait a few poll intervals
    await asyncio.sleep(0.2)
    assert manager.state == PrinterState.DISCONNECTED
    assert manager.target_mode is None


async def test_status_reflects_cover_open_after_poll(client: AsyncClient,
                                                     manager: ConnectionManager,
                                                     mock_printer: MockPrinter):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    # Let reconcile loop poll
    await wait_for(lambda: manager.state == PrinterState.ERROR, timeout=1.0)
    s = (await client.get("/status")).json()
    assert s["printer_state"] == PrinterState.ERROR.value
    assert s["device"]["cover"] == "open"


async def test_recovery_back_to_idle_when_fault_clears(client: AsyncClient,
                                                       manager: ConnectionManager,
                                                       mock_printer: MockPrinter):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    await wait_for(lambda: manager.state == PrinterState.ERROR, timeout=1.0)
    mock_printer.set_cover(False)
    await wait_for(lambda: manager.state == PrinterState.IDLE, timeout=1.0)


async def test_connect_failure_returns_503(client: AsyncClient,
                                           manager: ConnectionManager,
                                           mock_printer: MockPrinter):
    mock_printer.set_comm_error(True)
    r = await client.post("/connect", json={"mode": "lan"})
    assert r.status_code == 503
    assert manager.state == PrinterState.DISCONNECTED


async def test_reconcile_retries_after_connect_failure(client: AsyncClient,
                                                       manager: ConnectionManager,
                                                       mock_printer: MockPrinter):
    mock_printer.set_comm_error(True)
    r = await client.post("/connect", json={"mode": "lan"})
    assert r.status_code == 503
    # Now clear the fault; the loop should reconnect on its own
    mock_printer.set_comm_error(False)
    await wait_for(lambda: manager.state == PrinterState.IDLE, timeout=2.0)


async def test_breaker_opens_after_threshold_failures(client: AsyncClient,
                                                       manager: ConnectionManager,
                                                       mock_printer: MockPrinter,
                                                       settings):
    mock_printer.set_comm_error(True)
    # First attempt (user-triggered) returns 503
    r = await client.post("/connect", json={"mode": "lan"})
    assert r.status_code == 503
    # Now wait for reconcile to keep retrying and eventually trip the breaker
    await wait_for(
        lambda: manager.disconnect_reason == DisconnectReason.BREAKER_OPEN,
        timeout=3.0,
    )
    # Even after fault clears, breaker stays open — manual /connect required
    mock_printer.set_comm_error(False)
    await asyncio.sleep(0.3)
    assert manager.state == PrinterState.DISCONNECTED
    assert manager.disconnect_reason == DisconnectReason.BREAKER_OPEN
    # Manual reconnect clears it
    r2 = await client.post("/connect", json={"mode": "lan"})
    assert r2.status_code == 200
    assert manager.state == PrinterState.IDLE


async def test_mode_change_reconnects(client: AsyncClient,
                                      manager: ConnectionManager):
    await client.post("/connect", json={"mode": "lan"})
    assert manager.target_mode == "lan"
    r = await client.post("/connect", json={"mode": "usb"})
    assert r.status_code == 200
    assert manager.target_mode == "usb"
    s = (await client.get("/status")).json()
    assert s["connection"]["mode"] == "usb"


async def test_cached_status_does_not_block_on_device(client: AsyncClient,
                                                     manager: ConnectionManager):
    await client.post("/connect", json={"mode": "lan"})
    # /status reads cached → should be fast even if we manually acquire lock
    async with manager.lock():
        r = await client.get("/status")
        assert r.status_code == 200
        assert r.json()["activity"]["busy"] is True


async def test_invalid_mode_rejected_by_pydantic(client: AsyncClient):
    r = await client.post("/connect", json={"mode": "bluetooth"})
    assert r.status_code == 422
