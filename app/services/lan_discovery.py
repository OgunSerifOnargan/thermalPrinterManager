"""LAN discovery for Cashino-shaped ESC/POS printers.

Strategy:
  1. Detect the host's egress IPv4 (UDP-connect trick; no packets sent).
  2. Iterate the /24 around it concurrently.
  3. Each candidate gets a TCP probe on the printer port. If the port is
     open, send DLE EOT n=4 (`10 04 04`) — the real-time paper-sensor
     query — and check that the 1-byte reply has the Cashino-shaped
     reserved-bit pattern (bit 1 + bit 4 set, bits 0 + 7 clear).

Why a protocol probe: TCP-open alone over-reports (any service on 9100
qualifies). DLE EOT is real-time, bypasses the receive buffer, and
costs one round-trip — enough to weed out non-printers without becoming
intrusive.

stdlib only — `socket`, `asyncio`, `ipaddress`, `time`.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass

import psutil


log = logging.getLogger(__name__)


# ----- interface-name heuristics (psutil has no port-type API) -----
#
# Wi-Fi indicators — any iface containing one of these (case-insensitive)
# is treated as wireless and excluded by discover_cable().
_WIFI_HINTS = ("wlan", "wifi", "wi-fi", "wireless", "airport")

# Plumbing / virtual / tunnel adapters that should never be probed for
# a physical Cashino. Loopback (lo, lo0), macOS bridges (bridge*),
# AirDrop/AWDL, container bridges, VPN tunnels, etc.
_SKIP_HINTS = (
    "lo", "loopback",
    "bridge", "awdl", "llw",                # macOS
    "utun", "tap", "tun", "vmnet", "pdp_ip",   # tunnels / iOS sharing
    "veth", "docker", "br-",                # containers
    "ppp", "lxc",
)

# Reserved bits per ESC/POS DLE EOT n=4 spec: bit 1 + bit 4 = 1, bit 0
# + bit 7 = 0. The remaining bits are paper-sensor state; we don't care
# which combination, just that the fixed bits look right.
_CASHINO_MASK   = 0x93   # 1001 0011  — bits 0,1,4,7
_CASHINO_FIXED  = 0x12   # 0001 0010  — bit 1 + bit 4

_DEFAULT_PRINTER_PORT = 9100
_DEFAULT_TCP_TIMEOUT_S = 0.4
_DEFAULT_PROBE_TIMEOUT_S = 0.3


def _detect_local_ip() -> str | None:
    """Return the IPv4 the OS would route external traffic through.

    Opens a UDP socket and calls connect() — which does NOT send any
    packet for UDP, it just makes the kernel pick the egress interface.
    `getsockname()` then reports the local IP for that route. Robust on
    multi-NIC / VPN setups, doesn't need ifconfig parsing.

    Returns None when there is no usable network (e.g. inside a
    container with no external route, or airplane mode).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 8.8.8.8 is just a routable address; nothing is actually sent.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def _looks_like_cashino(byte: int) -> bool:
    """Return True if `byte` matches the fixed-bit pattern an ESC/POS
    DLE EOT n=4 reply is required to have. Doesn't validate the paper
    state — only the protocol shape."""
    return (byte & _CASHINO_MASK) == _CASHINO_FIXED


async def _probe(host: str, port: int, tcp_timeout_s: float,
                 probe_timeout_s: float) -> dict | None:
    """Probe one host. Returns None when the TCP connect refused or
    timed out (host filtered out completely). Returns a dict with
    `looks_like_cashino: bool` when the port was open."""
    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=tcp_timeout_s,
        )
    except (OSError, asyncio.TimeoutError):
        return None

    rtt_ms = int((time.monotonic() - start) * 1000)
    reply = b""
    try:
        writer.write(b"\x10\x04\x04")   # DLE EOT n=4 — paper sensor
        await writer.drain()
        try:
            reply = await asyncio.wait_for(reader.read(1),
                                           timeout=probe_timeout_s)
        except asyncio.TimeoutError:
            reply = b""
    except (OSError, ConnectionError):
        reply = b""
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:                       # noqa: BLE001
            pass

    if not reply:
        return {"host": host, "port": port, "rtt_ms": rtt_ms,
                "looks_like_cashino": False, "status_byte": None}
    byte = reply[0]
    return {"host": host, "port": port, "rtt_ms": rtt_ms,
            "looks_like_cashino": _looks_like_cashino(byte),
            "status_byte": f"0x{byte:02x}"}


async def discover(port: int = _DEFAULT_PRINTER_PORT,
                   tcp_timeout_s: float = _DEFAULT_TCP_TIMEOUT_S,
                   probe_timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
                   strict: bool = True,
                   local_ip_override: str | None = None,
                   subnet_override: ipaddress.IPv4Network | None = None,
                   ) -> dict:
    """Scan the local /24 subnet for Cashino-shaped TCP listeners.

    Parameters
    ----------
    port:
        Printer TCP port (defaults to 9100, the ESC/POS standard).
    tcp_timeout_s:
        Per-host TCP connect timeout. 254 hosts run in parallel so this
        bounds the *wall-clock* call, not per-host latency.
    probe_timeout_s:
        Per-host DLE EOT response timeout once TCP is open.
    strict:
        When True (default), only hosts whose reply matched the Cashino
        bit pattern are returned. When False, every TCP-open host is
        returned (debug / "what's on my network at all" mode).
    local_ip_override, subnet_override:
        Test hooks — let unit tests skip the kernel UDP detection and
        scan a synthetic subnet (e.g. a single 127.0.0.X address).

    Returns
    -------
    A dict with `local_ip`, `subnet`, `port`, `scanned`, `tcp_open`,
    `candidates`. When local-IP detection fails, an extra `reason`
    field is populated and `candidates` is empty.
    """
    if subnet_override is not None:
        subnet = subnet_override
        local_ip = local_ip_override or ""
    else:
        local_ip = local_ip_override or _detect_local_ip()
        if not local_ip:
            return {"local_ip": None, "subnet": None, "port": port,
                    "scanned": 0, "tcp_open": 0, "candidates": [],
                    "reason": "local_ip_undetected"}
        subnet = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)

    hosts = [str(ip) for ip in subnet.hosts() if str(ip) != local_ip]
    if not hosts:
        return {"local_ip": local_ip, "subnet": str(subnet), "port": port,
                "scanned": 0, "tcp_open": 0, "candidates": []}

    log.info("LAN discovery start",
             extra={"op": "discover_lan", "subnet": str(subnet),
                    "port": port, "hosts": len(hosts)})
    results = await asyncio.gather(
        *[_probe(h, port, tcp_timeout_s, probe_timeout_s) for h in hosts]
    )
    probed = [r for r in results if r is not None]
    candidates = [r for r in probed if r["looks_like_cashino"]] if strict else probed
    log.info("LAN discovery done",
             extra={"op": "discover_lan", "tcp_open": len(probed),
                    "candidates": len(candidates), "strict": strict})

    return {
        "local_ip": local_ip,
        "subnet": str(subnet),
        "port": port,
        "scanned": len(hosts),
        "tcp_open": len(probed),
        "candidates": candidates,
    }


# ---------------------------------------------------------------------------
# Cable-only discovery (for `mode: "lan_direct"` — printer plugged straight
# into the PC's Ethernet port, no router in between)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LocalSubnet:
    iface: str
    ip: str
    subnet: ipaddress.IPv4Network


def _iface_is_skippable(iface: str) -> bool:
    """Plumbing / virtual / tunnel interfaces we never want to scan."""
    low = iface.lower()
    return any(h in low for h in _SKIP_HINTS)


def _iface_is_wifi(iface: str) -> bool:
    """Best-effort wireless detector — psutil has no port-type API.
    macOS en0/en1 are *often* wireless on laptops; we don't assume that
    by index because docks/Thunderbolt change the order. Pattern names
    (wlan*, wlp*, "Wi-Fi", airport) are the safer signal."""
    low = iface.lower()
    return any(h in low for h in _WIFI_HINTS)


def _synthetic_dev_subnet() -> _LocalSubnet:
    """A loopback /29 the DEV-mode discovery scans as if it were a real
    cable interface. Lets the developer rehearse the lan_direct flow
    against the local mock device (127.0.0.1:9100) without an actual
    Cashino + Ethernet cable in the loop.

    The fake `ip` (127.0.0.255) is intentionally NOT a host in the
    /29 — so the self-filter doesn't accidentally exclude 127.0.0.1
    (where the mock listens)."""
    return _LocalSubnet(
        iface="dev-mock-cable",
        ip="127.0.0.255",
        subnet=ipaddress.IPv4Network("127.0.0.0/29"),  # hosts: .1 — .6
    )


def _enumerate_cable_subnets(include_wifi: bool = False,
                             cap_prefix: int = 24,
                             dev_mode: bool = False,
                             ) -> list[_LocalSubnet]:
    """Enumerate every active IPv4 interface that looks like a wired
    Ethernet port. WiFi and virtual / loopback / bridge / tunnel
    interfaces are skipped. The reported subnet is always clamped to
    `/cap_prefix` (default `/24`) — link-local 169.254/16 (APIPA)
    becomes a /24 scan around our own IP, so the wall-clock stays
    bounded even on link-local-only setups.

    When `dev_mode` is True, a synthetic loopback subnet is appended so
    a developer can rehearse the lan_direct flow against the mock
    device without a physical Ethernet cable. Production deployments
    set DEV_MODE=false, so this synthetic interface never leaks into
    a real customer's network scan."""
    out: list[_LocalSubnet] = []
    for iface, addrs in psutil.net_if_addrs().items():
        if _iface_is_skippable(iface):
            continue
        if not include_wifi and _iface_is_wifi(iface):
            continue
        for a in addrs:
            if a.family != socket.AF_INET:
                continue
            if not a.address or a.address.startswith("127."):
                continue
            try:
                net = ipaddress.IPv4Network(
                    f"{a.address}/{a.netmask or '255.255.255.0'}",
                    strict=False,
                )
            except (ValueError, TypeError):
                continue
            if net.prefixlen < cap_prefix:
                net = ipaddress.IPv4Network(f"{a.address}/{cap_prefix}",
                                            strict=False)
            out.append(_LocalSubnet(iface=iface, ip=a.address, subnet=net))
    if dev_mode:
        out.append(_synthetic_dev_subnet())
    return out


async def discover_cable(port: int = _DEFAULT_PRINTER_PORT,
                         tcp_timeout_s: float = _DEFAULT_TCP_TIMEOUT_S,
                         probe_timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
                         include_wifi: bool = False,
                         dev_mode: bool = False,
                         subnets_override: list[_LocalSubnet] | None = None,
                         ) -> dict:
    """Discover Cashino-shaped printers on every active wired interface.

    Designed for `POST /connect {"mode":"lan_direct"}` — the printer is
    plugged straight into the PC and we don't know the IP. Same TCP +
    DLE-EOT probe as `discover()`, but the scanned subnet comes from
    every wired NIC psutil reports, not from the default route. Each
    candidate carries a `via_interface` field so the caller can tell
    the user which port the printer answered on.

    `subnets_override` is a test hook (lets unit tests skip psutil).
    """
    subnets = (subnets_override if subnets_override is not None
               else _enumerate_cable_subnets(include_wifi=include_wifi,
                                             dev_mode=dev_mode))
    if not subnets:
        return {"interfaces": [], "port": port, "scanned": 0,
                "tcp_open": 0, "candidates": [],
                "reason": "no_cable_interface"}

    self_ips = {s.ip for s in subnets}
    host_to_iface: dict[str, str] = {}
    tasks = []
    for sn in subnets:
        for ip in sn.subnet.hosts():
            host = str(ip)
            if host in self_ips or host in host_to_iface:
                continue
            host_to_iface[host] = sn.iface
            tasks.append(_probe(host, port, tcp_timeout_s, probe_timeout_s))

    log.info("cable discovery start",
             extra={"op": "discover_cable",
                    "interfaces": len(subnets), "hosts": len(tasks)})
    results = await asyncio.gather(*tasks) if tasks else []
    probed = [r for r in results if r is not None]
    for r in probed:
        r["via_interface"] = host_to_iface.get(r["host"])
    candidates = [r for r in probed if r["looks_like_cashino"]]
    log.info("cable discovery done",
             extra={"op": "discover_cable", "tcp_open": len(probed),
                    "candidates": len(candidates)})

    return {
        "interfaces": [{"iface": s.iface, "ip": s.ip, "subnet": str(s.subnet)}
                        for s in subnets],
        "port": port,
        "scanned": len(tasks),
        "tcp_open": len(probed),
        "candidates": candidates,
    }
