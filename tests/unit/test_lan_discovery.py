"""Unit tests for app.services.lan_discovery.

Covers the local-IP detector and the per-host probe — the public
`discover()` entry point is exercised in integration tests through the
`/discover/lan` HTTP endpoint.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from unittest.mock import patch

import pytest

from app.services import lan_discovery
from tests._helpers.fake_printer_server import (
    FakePrinterServer,
    reserve_unused_port,
)


# ----- _detect_local_ip -----

def test_detect_local_ip_returns_v4_string() -> None:
    """On any host with a routable network (CI, dev laptop), the UDP
    trick returns a dotted-quad IPv4 string."""
    ip = lan_discovery._detect_local_ip()
    # Some CI runners have no egress at all → None is acceptable.
    if ip is None:
        pytest.skip("no usable egress route on this host")
    assert isinstance(ip, str)
    # Must parse as a valid IPv4 address.
    ipaddress.IPv4Address(ip)


def test_detect_local_ip_returns_none_when_no_network() -> None:
    """If the kernel refuses the UDP connect (OSError), the helper
    swallows it and returns None — never raises into the caller."""

    real_socket = socket.socket

    class BrokenSocket:
        def __init__(self, *_a, **_kw) -> None:
            pass
        def connect(self, *_a, **_kw) -> None:
            raise OSError("no route to host")
        def getsockname(self) -> tuple[str, int]:  # pragma: no cover
            raise AssertionError("must not be called when connect failed")
        def close(self) -> None:
            pass

    with patch.object(socket, "socket", BrokenSocket):
        assert lan_discovery._detect_local_ip() is None


# ----- _looks_like_cashino -----

@pytest.mark.parametrize("byte", [
    0x12,  # clean printer (just reserved bits)
    0x1E,  # paper near-end (reserved + bits 2,3)
    0x72,  # paper end (reserved + bits 5,6)
    0x16,  # cover open (reserved + bit 2)
])
def test_looks_like_cashino_accepts_realistic_status_bytes(byte: int) -> None:
    assert lan_discovery._looks_like_cashino(byte)


@pytest.mark.parametrize("byte", [
    0x00,  # all-zero — no reserved bits set
    0xFF,  # all-ones — bit 0 and bit 7 forbidden
    0x80,  # bit 7 set
    0x01,  # bit 0 set, no reserved bits
    0x10,  # only bit 4, missing bit 1
    0x02,  # only bit 1, missing bit 4
])
def test_looks_like_cashino_rejects_non_cashino_bytes(byte: int) -> None:
    assert not lan_discovery._looks_like_cashino(byte)


# ----- _probe -----

@pytest.mark.asyncio
async def test_probe_matches_cashino_byte() -> None:
    async with FakePrinterServer(reply_byte=0x1E) as srv:
        host, port = srv.address
        result = await lan_discovery._probe(host, port, 1.0, 1.0)
    assert result is not None
    assert result["looks_like_cashino"] is True
    assert result["status_byte"] == "0x1e"
    assert isinstance(result["rtt_ms"], int)
    assert result["host"] == host and result["port"] == port


@pytest.mark.asyncio
async def test_probe_rejects_non_cashino_byte() -> None:
    async with FakePrinterServer(reply_byte=0xFF) as srv:
        host, port = srv.address
        result = await lan_discovery._probe(host, port, 1.0, 1.0)
    assert result is not None
    assert result["looks_like_cashino"] is False
    assert result["status_byte"] == "0xff"


@pytest.mark.asyncio
async def test_probe_handles_silent_server() -> None:
    """Server accepts the TCP connect but never replies to DLE EOT —
    the probe returns the TCP-open record with status_byte=None and
    looks_like_cashino=False."""
    async with FakePrinterServer(reply_byte=None) as srv:
        host, port = srv.address
        result = await lan_discovery._probe(host, port, 1.0, 0.2)
    assert result is not None
    assert result["status_byte"] is None
    assert result["looks_like_cashino"] is False


@pytest.mark.asyncio
async def test_probe_handles_closed_port() -> None:
    """No listener — connect refused, probe returns None so the host
    is filtered out completely."""
    closed_port = reserve_unused_port()
    result = await lan_discovery._probe("127.0.0.1", closed_port, 0.5, 0.5)
    assert result is None


# ----- discover() — exercised end-to-end via subnet_override -----

@pytest.mark.asyncio
async def test_discover_with_subnet_override_returns_strict_candidate() -> None:
    """Use the subnet_override hook to point discover() at a /30
    that includes the fake server's loopback address — avoids
    scanning the host's real subnet during tests."""
    async with FakePrinterServer(reply_byte=0x12) as srv:
        host, port = srv.address
        # /30 around 127.0.0.1 has 2 usable hosts: .1 and .2.
        subnet = ipaddress.IPv4Network("127.0.0.0/30")
        result = await lan_discovery.discover(
            port=port,
            tcp_timeout_s=0.5,
            probe_timeout_s=0.5,
            strict=True,
            local_ip_override="",         # don't filter "self"
            subnet_override=subnet,
        )
    assert result["candidates"]
    assert any(c["host"] == host for c in result["candidates"])
    assert result["tcp_open"] >= 1


@pytest.mark.asyncio
async def test_discover_strict_filters_out_non_cashino() -> None:
    async with FakePrinterServer(reply_byte=0xFF) as srv:
        port = srv.address[1]
        subnet = ipaddress.IPv4Network("127.0.0.0/30")
        strict = await lan_discovery.discover(
            port=port, tcp_timeout_s=0.5, probe_timeout_s=0.5,
            strict=True, local_ip_override="", subnet_override=subnet,
        )
        loose = await lan_discovery.discover(
            port=port, tcp_timeout_s=0.5, probe_timeout_s=0.5,
            strict=False, local_ip_override="", subnet_override=subnet,
        )
    assert strict["candidates"] == []
    assert loose["candidates"]
    assert loose["tcp_open"] >= 1


@pytest.mark.asyncio
async def test_discover_returns_reason_when_local_ip_undetected() -> None:
    """When `_detect_local_ip` returns None and no override is given,
    discover() reports a reason field and an empty candidates list
    instead of crashing or hanging."""
    with patch.object(lan_discovery, "_detect_local_ip", return_value=None):
        result = await lan_discovery.discover()
    assert result["local_ip"] is None
    assert result["subnet"] is None
    assert result["candidates"] == []
    assert result.get("reason") == "local_ip_undetected"


# ----- cable-only enumeration (lan_direct support) -----


def _fake_snicaddr(family: int, address: str | None,
                  netmask: str | None = "255.255.255.0"):
    """Minimal shim mirroring psutil.snicaddr's duck-type so we can
    inject synthetic adapter listings without monkey-patching psutil
    private attrs."""
    from types import SimpleNamespace
    return SimpleNamespace(family=family, address=address,
                           netmask=netmask, broadcast=None, ptp=None)


def test_enumerate_cable_skips_loopback_and_virtual() -> None:
    """Loopback (`lo0`), bridges, AWDL and the lot must never appear
    in the cable list — they're not user-facing ports."""
    fake = {
        "lo0":     [_fake_snicaddr(socket.AF_INET, "127.0.0.1", "255.0.0.0")],
        "bridge0": [_fake_snicaddr(socket.AF_INET, "192.168.64.1")],
        "awdl0":   [_fake_snicaddr(socket.AF_INET, "169.254.1.1")],
        "docker0": [_fake_snicaddr(socket.AF_INET, "172.17.0.1")],
        "en7":     [_fake_snicaddr(socket.AF_INET, "192.168.1.20")],
    }
    with patch("psutil.net_if_addrs", return_value=fake):
        out = lan_discovery._enumerate_cable_subnets()
    names = {s.iface for s in out}
    assert names == {"en7"}, f"unexpected: {names}"


def test_enumerate_cable_skips_wifi_by_default() -> None:
    """Wi-Fi-named interfaces (`wlan0`, `wlp3s0`) are filtered out
    when include_wifi=False (default for lan_direct)."""
    fake = {
        "wlan0": [_fake_snicaddr(socket.AF_INET, "192.168.1.10")],
        "en7":   [_fake_snicaddr(socket.AF_INET, "169.254.5.6")],
    }
    with patch("psutil.net_if_addrs", return_value=fake):
        cable_only = lan_discovery._enumerate_cable_subnets()
        with_wifi  = lan_discovery._enumerate_cable_subnets(include_wifi=True)
    assert {s.iface for s in cable_only} == {"en7"}
    assert {s.iface for s in with_wifi} == {"wlan0", "en7"}


def test_enumerate_cable_clamps_subnet_to_24() -> None:
    """Link-local APIPA reports /16; we must clamp it to /24 so the
    sweep stays bounded (no 65k-host scans)."""
    fake = {
        "en7": [_fake_snicaddr(socket.AF_INET, "169.254.42.20",
                                "255.255.0.0")],  # /16
    }
    with patch("psutil.net_if_addrs", return_value=fake):
        out = lan_discovery._enumerate_cable_subnets()
    assert len(out) == 1
    assert out[0].subnet.prefixlen == 24
    assert out[0].subnet.network_address == ipaddress.IPv4Address("169.254.42.0")


@pytest.mark.asyncio
async def test_discover_cable_returns_via_interface_on_candidate() -> None:
    """A Cashino-shaped server in a synthetic interface's subnet must
    carry that interface's name in the candidate's `via_interface`
    field — so the UI can tell the user which port the printer
    answered on."""
    async with FakePrinterServer(reply_byte=0x12) as srv:
        host, port = srv.address
        subnets = [lan_discovery._LocalSubnet(
            iface="en9", ip="127.0.0.10",
            subnet=ipaddress.IPv4Network("127.0.0.0/30"),
        )]
        result = await lan_discovery.discover_cable(
            port=port, tcp_timeout_s=0.5, probe_timeout_s=0.5,
            subnets_override=subnets,
        )
    assert result["candidates"], "no candidate returned"
    assert any(c["via_interface"] == "en9" for c in result["candidates"])


@pytest.mark.asyncio
async def test_discover_cable_with_no_interfaces_returns_reason() -> None:
    """psutil reports zero usable interfaces (e.g. inside a stripped
    container) → empty candidates + a `reason` field, not a crash."""
    result = await lan_discovery.discover_cable(subnets_override=[])
    assert result["candidates"] == []
    assert result["interfaces"] == []
    assert result.get("reason") == "no_cable_interface"


def test_enumerate_cable_appends_dev_mock_when_dev_mode() -> None:
    """In DEV_MODE the enumeration injects a synthetic loopback subnet
    so the lan_direct flow can be rehearsed against the local mock
    device without an actual Cashino. Production (dev_mode=False) must
    NOT see this synthetic interface."""
    fake = {
        "lo0": [_fake_snicaddr(socket.AF_INET, "127.0.0.1", "255.0.0.0")],
        "en7": [_fake_snicaddr(socket.AF_INET, "192.168.1.20")],
    }
    with patch("psutil.net_if_addrs", return_value=fake):
        prod  = lan_discovery._enumerate_cable_subnets(dev_mode=False)
        devm  = lan_discovery._enumerate_cable_subnets(dev_mode=True)
    prod_names = {s.iface for s in prod}
    dev_names  = {s.iface for s in devm}
    assert prod_names == {"en7"}
    assert dev_names == {"en7", "dev-mock-cable"}
    # The synthetic subnet must cover 127.0.0.1 (where the mock listens)
    # and use a fake "ip" outside that range (so self-filter doesn't
    # accidentally exclude 127.0.0.1).
    mock_sn = next(s for s in devm if s.iface == "dev-mock-cable")
    assert ipaddress.IPv4Address("127.0.0.1") in mock_sn.subnet.hosts()
    assert mock_sn.ip != "127.0.0.1"


@pytest.mark.asyncio
async def test_discover_cable_dev_mode_finds_mock_on_loopback() -> None:
    """A Cashino-shaped server bound to 127.0.0.1 must show up as a
    candidate when discover_cable(dev_mode=True). This is the actual
    rehearsal path: developer runs the mock + service locally, calls
    discover, picks the result."""
    import socket as _socket
    # Bind to 127.0.0.1 *with port 9100 if free*, else fallback to
    # whatever the FakePrinterServer grabs (still on 127.0.0.1).
    async with FakePrinterServer(reply_byte=0x12, host="127.0.0.1") as srv:
        host, port = srv.address
        # No psutil mock — we override subnets directly so the test is
        # not coupled to the host's real NIC list.
        subnets = [lan_discovery._synthetic_dev_subnet()]
        result = await lan_discovery.discover_cable(
            port=port, tcp_timeout_s=0.5, probe_timeout_s=0.5,
            subnets_override=subnets,
        )
    assert any(c["host"] == "127.0.0.1" for c in result["candidates"]), (
        f"expected 127.0.0.1 in candidates, got {result['candidates']}"
    )
