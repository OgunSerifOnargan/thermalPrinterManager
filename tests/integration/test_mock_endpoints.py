"""Integration: /mock/* endpoints only mounted in DEV_MODE."""
from __future__ import annotations

import asyncio

from httpx import AsyncClient

from app.devices.mock_printer import MockPrinter


async def wait_for(predicate, timeout: float = 1.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timeout")


async def test_set_paper(client: AsyncClient, mock_printer: MockPrinter):
    # Register mock_printer in app state (test conftest doesn't auto-set this)
    from app.main import app
    app.state.mock_printer = mock_printer

    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/mock/set_paper", json={"lines": 5})
    assert r.status_code == 200
    assert r.json()["paper_lines"] == 5
    assert mock_printer.paper_lines == 5


async def test_set_cover_triggers_error_via_poll(client: AsyncClient,
                                                 mock_printer: MockPrinter, manager):
    from app.main import app
    app.state.mock_printer = mock_printer
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/mock/set_cover", json={"open": True})
    assert r.status_code == 200
    await wait_for(lambda: manager.cached.cover == "open", timeout=1.0)


async def test_run_scenario_overheat(client: AsyncClient, mock_printer: MockPrinter, manager):
    from app.main import app
    app.state.mock_printer = mock_printer
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/mock/run_scenario", json={"scenario": "overheat"})
    assert r.status_code == 200
    assert mock_printer.overheated() is True


async def test_run_scenario_recover_all(client: AsyncClient,
                                        mock_printer: MockPrinter, manager):
    from app.main import app
    app.state.mock_printer = mock_printer
    mock_printer.set_paper(0)
    mock_printer.set_cover(True)
    mock_printer.set_jammed(True)
    mock_printer.set_temperature(80.0)
    r = await client.post("/mock/run_scenario", json={"scenario": "recover_all"})
    assert r.status_code == 200
    assert mock_printer.paper_lines > 0
    assert mock_printer.cover_open is False
    assert mock_printer.jammed is False
    assert mock_printer.overheated() is False


async def test_dev_mode_off_hides_mock_endpoints(client: AsyncClient,
                                                 mock_printer: MockPrinter,
                                                 monkeypatch, env):
    """When DEV_MODE=false, /mock/* must return 404."""
    from app.main import app
    app.state.mock_printer = mock_printer
    env.setenv("DEV_MODE", "false")
    from app.core.config import reset_settings_for_test
    reset_settings_for_test()
    r = await client.post("/mock/set_paper", json={"lines": 10})
    assert r.status_code == 404
