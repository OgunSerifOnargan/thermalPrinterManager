"""End-to-end tests for `POST /connect {"mode":"lan_direct"}`.

The lan_direct branch delegates to `lan_discovery.discover_cable()`
to find the printer, then calls the normal ConnectionManager path with
the resolved host/port. These tests patch `discover_cable` so the
candidate set can be controlled deterministically:

  * 0 candidates → 503 NO_DIRECT_DEVICE + interfaces list
  * 1 candidate  → 200 + resolved {host, port, via_interface, rtt_ms}
                   AND the manager's request_connect was invoked with
                   the resolved host (so reconcile / cached status /
                   INIT-on-connect all flow as in manual `mode=lan`).
  * N>1          → 409 MULTIPLE_CANDIDATES + candidates list in body
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


_INTERFACES = [
    {"iface": "en7", "ip": "169.254.42.10", "subnet": "169.254.42.0/24"},
]


def _candidate(host: str, port: int = 9100, iface: str = "en7",
               status_byte: str = "0x12") -> dict:
    return {
        "host": host, "port": port, "rtt_ms": 4,
        "looks_like_cashino": True, "status_byte": status_byte,
        "via_interface": iface,
    }


@pytest.mark.asyncio
async def test_lan_direct_no_candidate_returns_503(client: AsyncClient) -> None:
    """When discover_cable finds nothing, /connect returns 503 with
    the NO_DIRECT_DEVICE error code and a list of the interfaces it
    *did* scan (useful for the user to verify the cable is in the right
    port)."""
    fake_result = {"interfaces": _INTERFACES, "port": 9100,
                   "scanned": 253, "tcp_open": 0, "candidates": []}
    with patch("app.services.lan_discovery.discover_cable",
               new=AsyncMock(return_value=fake_result)):
        r = await client.post("/connect", json={"mode": "lan_direct"})
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "NO_DIRECT_DEVICE"
    assert body["interfaces"] == _INTERFACES
    assert "message_tr" in body and "message_en" in body


@pytest.mark.asyncio
async def test_lan_direct_one_candidate_auto_connects(
    client: AsyncClient,
) -> None:
    """One candidate → auto-connect path. /connect returns 200 with the
    resolved host/port/interface; the underlying ConnectionManager was
    asked to connect via lan with overrides containing that host."""
    cand = _candidate("169.254.42.50")
    fake_result = {"interfaces": _INTERFACES, "port": 9100,
                   "scanned": 253, "tcp_open": 1, "candidates": [cand]}

    # Spy on the manager so we can assert it was called with the
    # resolved overrides. The MockTransport already in place makes the
    # actual connect a no-op.
    from app.services.connection_manager import ConnectionManager
    real_request_connect = ConnectionManager.request_connect
    seen: dict = {}

    async def _spy(self, mode, overrides=None):  # type: ignore[no-untyped-def]
        seen["mode"] = mode
        seen["overrides"] = overrides
        return await real_request_connect(self, mode, overrides=overrides)

    with patch("app.services.lan_discovery.discover_cable",
               new=AsyncMock(return_value=fake_result)), \
         patch.object(ConnectionManager, "request_connect", _spy):
        r = await client.post("/connect", json={"mode": "lan_direct"})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "lan_direct"
    assert body["resolved"]["host"] == "169.254.42.50"
    assert body["resolved"]["port"] == 9100
    assert body["resolved"]["via_interface"] == "en7"
    assert body["resolved"]["rtt_ms"] == 4

    # The lan_direct branch must hand off to the normal lan path:
    # mode=lan with the resolved overrides, NOT mode=lan_direct.
    assert seen["mode"] == "lan"
    assert seen["overrides"] == {"lan_host": "169.254.42.50", "lan_port": 9100}


@pytest.mark.asyncio
async def test_lan_direct_multiple_candidates_returns_409_with_list(
    client: AsyncClient,
) -> None:
    """Two or more candidates → 409 MULTIPLE_CANDIDATES. The body
    carries the full candidates list so the UI can render a picker.
    No auto-connect happens — the user must choose."""
    cands = [
        _candidate("169.254.42.50", iface="en7"),
        _candidate("192.168.7.40", iface="en9"),
    ]
    fake_result = {"interfaces": _INTERFACES + [
        {"iface": "en9", "ip": "192.168.7.20", "subnet": "192.168.7.0/24"}
    ], "port": 9100, "scanned": 506, "tcp_open": 2, "candidates": cands}

    from app.services.connection_manager import ConnectionManager
    connect_called = False

    async def _spy(self, *a, **kw):  # type: ignore[no-untyped-def]
        nonlocal connect_called
        connect_called = True

    with patch("app.services.lan_discovery.discover_cable",
               new=AsyncMock(return_value=fake_result)), \
         patch.object(ConnectionManager, "request_connect", _spy):
        r = await client.post("/connect", json={"mode": "lan_direct"})

    assert r.status_code == 409
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "MULTIPLE_CANDIDATES"
    assert len(body["candidates"]) == 2
    assert {c["host"] for c in body["candidates"]} == {
        "169.254.42.50", "192.168.7.40",
    }
    assert connect_called is False, (
        "must not auto-connect when more than one candidate matched"
    )
