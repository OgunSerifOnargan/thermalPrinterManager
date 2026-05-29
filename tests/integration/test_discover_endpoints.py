"""Integration tests for /discover/usb and /discover/lan.

The USB tests piggyback on the existing inventory helper. The LAN
tests patch `lan_discovery.discover` to inject a synthetic result —
unit tests already exercise the real socket path; here we just verify
the endpoint wiring (query params reach the service, response body is
returned untouched, /usb/devices and /discover/usb agree).
"""
from __future__ import annotations

from unittest.mock import patch, AsyncMock

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_discover_usb_alias_returns_same_body_as_usb_devices(
    client: AsyncClient,
) -> None:
    r1 = await client.get("/usb/devices")
    r2 = await client.get("/discover/usb")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()


@pytest.mark.asyncio
async def test_discover_lan_passes_query_params_to_service(
    client: AsyncClient,
) -> None:
    """The route's `port` and `strict` query params must reach
    `lan_discovery.discover`; the response body is whatever the service
    returns, untouched."""
    fake_body = {
        "local_ip": "10.0.0.5",
        "subnet": "10.0.0.0/24",
        "port": 4242,
        "scanned": 0,
        "tcp_open": 0,
        "candidates": [],
    }
    with patch("app.services.lan_discovery.discover",
               new=AsyncMock(return_value=fake_body)) as m:
        r = await client.get("/discover/lan?port=4242&strict=false")
    assert r.status_code == 200
    assert r.json() == fake_body
    m.assert_awaited_once()
    kwargs = m.await_args.kwargs
    assert kwargs["port"] == 4242
    assert kwargs["strict"] is False


@pytest.mark.asyncio
async def test_discover_lan_uses_defaults_when_no_query_params(
    client: AsyncClient,
) -> None:
    with patch("app.services.lan_discovery.discover",
               new=AsyncMock(return_value={"candidates": []})) as m:
        r = await client.get("/discover/lan")
    assert r.status_code == 200
    kwargs = m.await_args.kwargs
    assert kwargs["port"] == 9100      # default printer port
    assert kwargs["strict"] is True     # default safety filter


@pytest.mark.asyncio
async def test_discover_cable_endpoint_returns_candidates_without_connecting(
    client: AsyncClient,
) -> None:
    """`GET /discover/cable` must return the discover_cable() body
    untouched and NOT change connection state — it's the UI's picker
    feeder, not a connect call."""
    cands = [{"host": "127.0.0.1", "port": 9100, "rtt_ms": 1,
              "looks_like_cashino": True, "status_byte": "0x12",
              "via_interface": "dev-mock-cable"}]
    fake = {"interfaces": [{"iface": "dev-mock-cable",
                            "ip": "127.0.0.255",
                            "subnet": "127.0.0.0/29"}],
            "port": 9100, "scanned": 6, "tcp_open": 1,
            "candidates": cands}
    with patch("app.services.lan_discovery.discover_cable",
               new=AsyncMock(return_value=fake)) as m:
        r = await client.get("/discover/cable")
    assert r.status_code == 200
    assert r.json() == fake
    m.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_cable_endpoint_forwards_dev_mode_flag(
    client: AsyncClient, settings,
) -> None:
    """The endpoint must read `settings.dev_mode` and pass it through
    to discover_cable, so the synthetic loopback interface only ever
    appears in dev environments."""
    with patch("app.services.lan_discovery.discover_cable",
               new=AsyncMock(return_value={"candidates": []})) as m:
        await client.get("/discover/cable")
    # Test settings (conftest) default DEV_MODE is True
    assert m.await_args.kwargs["dev_mode"] is settings.dev_mode
