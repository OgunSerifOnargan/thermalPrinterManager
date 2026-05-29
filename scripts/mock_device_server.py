"""Standalone stateful mock thermal printer device.

Runs as its own process to validate the LAN code path end-to-end. Reuses
the MockPrinter brain from app.devices so behavior is identical to the
in-process mock — only the transport changes.

Two ports:

  9100/tcp   ESC/POS data + DLE EOT status, like a real Cashino over LAN.
  9101/http  Control API: paper, cover, jam, temperature, comm-error,
             scenario presets. Includes a tiny live dashboard at /.

Usage::

    python scripts/mock_device_server.py
    python scripts/mock_device_server.py --tcp-port 9100 --http-port 9101

Then in the main service's .env set:

    TRANSPORT_BACKEND=real
    LAN_HOST=127.0.0.1
    LAN_PORT=9100
    MOCK_DEVICE_CONTROL_URL=http://127.0.0.1:9101
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Literal

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.devices.mock_printer import MockPrinter, MockPrinterConfig


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mock-device")


# ---------- Device profiles (from Cashino datasheets) ----------
# Same codebase emulates the whole family — profile selects the physics
# differences (overheat hysteresis, paper width, thermal head behavior).

DEVICE_PROFILES: dict[str, dict] = {
    "KP-300": {
        "label": "KP-300 — 58mm, 32 cols, basic",
        "paper_width_mm": 58,
        "paper_width_cols": 32,
        "paper_initial_lines": 500,
        "paper_low_threshold": 50,
        "overheat_stop_c": 65.0,
        "overheat_resume_c": 55.0,
        "heat_rate": 2.0,
        "cool_rate": 0.5,
        "supports_qr": True,
        "supports_partial_cut": True,
    },
    "KP-301H": {
        "label": "KP-301H — 80mm, 48 cols, fast head",
        "paper_width_mm": 80,
        "paper_width_cols": 48,
        "paper_initial_lines": 700,
        "paper_low_threshold": 70,
        "overheat_stop_c": 65.0,
        "overheat_resume_c": 60.0,   # tighter hysteresis per datasheet
        "heat_rate": 2.2,
        "cool_rate": 0.4,
        "supports_qr": True,
        "supports_partial_cut": True,
    },
    "KP-302": {
        "label": "KP-302 — 58mm, 32 cols, default",
        "paper_width_mm": 58,
        "paper_width_cols": 32,
        "paper_initial_lines": 500,
        "paper_low_threshold": 50,
        "overheat_stop_c": 65.0,
        "overheat_resume_c": 55.0,
        "heat_rate": 2.0,
        "cool_rate": 0.5,
        "supports_qr": True,
        "supports_partial_cut": True,
    },
}

_current_profile_name: str = "KP-302"


def _apply_profile(name: str) -> dict:
    """Mutate the brain's config to match the named device profile.

    Paper level is NOT reset — user keeps whatever count they have. Only the
    physics knobs (thresholds, rates, paper-low threshold) change. The
    overheated flag is re-evaluated against the new hysteresis on next read.
    """
    global _current_profile_name
    if name not in DEVICE_PROFILES:
        raise ValueError(f"unknown device profile: {name!r}")
    p = DEVICE_PROFILES[name]
    cfg = brain._cfg  # noqa: SLF001 — mock owns the brain
    cfg.overheat_stop_c = p["overheat_stop_c"]
    cfg.overheat_resume_c = p["overheat_resume_c"]
    cfg.heat_rate = p["heat_rate"]
    cfg.cool_rate = p["cool_rate"]
    cfg.paper_low_threshold = p["paper_low_threshold"]
    _current_profile_name = name
    # Re-evaluate hysteresis with new thresholds (cheap, just compares state).
    brain.set_temperature(brain.temperature())
    log.info("device profile applied: %s", name)
    return p


# ---------- Shared brain (singleton inside this process) ----------

brain = MockPrinter(config=MockPrinterConfig(
    paper_initial_lines=DEVICE_PROFILES["KP-302"]["paper_initial_lines"],
    paper_low_threshold=DEVICE_PROFILES["KP-302"]["paper_low_threshold"],
    initial_temp=25.0,
    overheat_stop_c=DEVICE_PROFILES["KP-302"]["overheat_stop_c"],
    overheat_resume_c=DEVICE_PROFILES["KP-302"]["overheat_resume_c"],
    heat_rate=DEVICE_PROFILES["KP-302"]["heat_rate"],
    cool_rate=DEVICE_PROFILES["KP-302"]["cool_rate"],
))
active_writers: set[asyncio.StreamWriter] = set()

# PTY (virtual USB-CDC) state — populated when --enable-pty is on
_pty_state: dict = {"enabled": False, "path": None, "symlink": None,
                    "master_fd": None, "slave_fd": None,
                    "intended_symlink": None,    # persists across close/reopen
                    "bytes_in": 0, "bytes_out": 0,
                    "status_queries": 0, "print_bytes": 0, "prints_received": 0,
                    "fence_responses": 0}

# Each LF feeds ~10 ms of "print time" — Cashino KP-302 spec is 250 mm/s on
# ~3 mm lines, so ~12.5 ms/line. 10 ms gives a tidy round number for demos
# without dragging out the wait. Override via --print-delay-ms.
_PRINT_DELAY_MS_PER_LINE = 10


# ---------- ESC/POS-aware command parser ----------
#
# Real Cashino firmware doesn't misfire on `10 04` (DLE EOT) or `1D 72` (GS r)
# bytes when they happen to occur inside the *data* of a buffered command —
# raster image rows, QR storage payloads, bit-image data, etc. The interpreter
# is in a data-receive state and consumes the configured byte count blindly.
#
# Our naïve scanner did fire on those accidental sequences and replied with a
# stale status byte, which then leaked into the next real DLE EOT n=3 read
# on the host side and decoded as `bit 2 = jammed` → false PAPER_JAM.
#
# Below: a small stateful parser shared by both TCP and PTY handlers. It
# carries `remaining_data_bytes` across chunks so a command whose data section
# straddles a 4 KB read still skips correctly.

class _EscPosScanner:
    """Stateful byte scanner that recognises a few variable-length commands
    and yields (event, payload) tuples. Designed to be fed chunk-by-chunk."""

    # event types
    PRINT = "print"          # payload: bytes — feed to brain.feed_data
    DLE_EOT = "dle_eot"      # payload: int — register number
    GS_R = "gs_r"            # payload: int — register number

    def __init__(self) -> None:
        self._remaining_data = 0
        self._pending = bytearray()   # partial header carried across chunks

    @staticmethod
    def _data_section_length(buf: bytes, i: int) -> tuple[int, int] | None:
        """If buf[i:] begins with a recognized variable-data command and we
        have enough bytes for its header, return (header_len, data_len).
        Return None when not a match. Raise IndexError-style 'wait' by
        returning ('need', need_bytes) so the caller can buffer.
        """
        n = len(buf)
        if i >= n:
            return None
        b0 = buf[i]
        # GS v 0 m xL xH yL yH d... — raster bit image
        if b0 == 0x1D and i + 1 < n and buf[i + 1] == 0x76:
            need = 8
            if i + need > n:
                return ("need", need)
            if buf[i + 2] != 0x30:
                return None
            xL, xH, yL, yH = buf[i + 4], buf[i + 5], buf[i + 6], buf[i + 7]
            data = (xL + (xH << 8)) * (yL + (yH << 8))
            return (8, data)
        # GS ( k pL pH cn fn d... — variable cluster (QR, NV graphics, etc.)
        if b0 == 0x1D and i + 1 < n and buf[i + 1] == 0x28:
            need = 5
            if i + need > n:
                return ("need", need)
            if buf[i + 2] != 0x6B:
                return None
            pL, pH = buf[i + 3], buf[i + 4]
            return (5, pL + (pH << 8))
        # ESC * m nL nH d... — bit-image
        if b0 == 0x1B and i + 1 < n and buf[i + 1] == 0x2A:
            need = 5
            if i + need > n:
                return ("need", need)
            m = buf[i + 2]
            units = buf[i + 3] + (buf[i + 4] << 8)
            data = units * 3 if m in (32, 33) else units
            return (5, data)
        # GS * x y d... — define downloaded bit-image
        if b0 == 0x1D and i + 1 < n and buf[i + 1] == 0x2A:
            need = 4
            if i + need > n:
                return ("need", need)
            x, y = buf[i + 2], buf[i + 3]
            return (4, x * y * 8)
        return None

    def feed(self, chunk: bytes):
        """Yield (event, payload) tuples for the bytes consumable from this
        chunk. Anything that can't be decided yet (partial header, partial
        data) is carried in internal state for the next feed."""
        buf = bytes(self._pending) + chunk
        self._pending.clear()
        i = 0
        print_start = 0
        n = len(buf)
        while i < n:
            # If we're inside a data section of a known command, just
            # forward bytes to the print sink and decrement the counter.
            if self._remaining_data > 0:
                take = min(self._remaining_data, n - i)
                self._remaining_data -= take
                i += take
                continue
            # DLE EOT n — real-time, 3 bytes
            if buf[i] == 0x10 and i + 2 < n and buf[i + 1] == 0x04:
                if print_start < i:
                    yield (self.PRINT, buf[print_start:i])
                yield (self.DLE_EOT, buf[i + 2])
                i += 3
                print_start = i
                continue
            if buf[i] == 0x10 and i + 2 >= n:
                # Partial DLE EOT — wait for the rest in next chunk.
                break
            # GS r n — buffered fence, 3 bytes
            if buf[i] == 0x1D and i + 2 < n and buf[i + 1] == 0x72:
                if print_start < i:
                    yield (self.PRINT, buf[print_start:i])
                yield (self.GS_R, buf[i + 2])
                i += 3
                print_start = i
                continue
            # Variable-data commands (raster, QR, bit-image): skip header +
            # data so accidental 10 04 / 1D 72 inside payload don't fire.
            ds = self._data_section_length(buf, i)
            if ds is not None:
                if ds[0] == "need":
                    # Header straddles chunk boundary; defer until next feed.
                    break
                header_len, data_len = ds
                end = i + header_len + data_len
                if end > n:
                    # Header is complete; data spans next chunk(s).
                    self._remaining_data = end - n
                    i = n
                    continue
                # Whole header+data fits in this chunk — consume it.
                i = end
                continue
            i += 1
        # Anything we haven't decided yet is "print data so far" + a maybe-
        # partial command tail at the very end.
        if print_start < i:
            yield (self.PRINT, buf[print_start:i])
        if i < n:
            # Partial command bytes — stash for next chunk.
            self._pending.extend(buf[i:])


# ---------- TCP printer port (9100) ----------

async def handle_tcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    addr = writer.get_extra_info("peername")
    log.info("printer-tcp open from %s", addr)
    active_writers.add(writer)
    scanner = _EscPosScanner()
    # Track lines printed since the last fence so the fence response delay
    # reflects real Cashino timing (~10 ms/line at 250 mm/s).
    lines_pending_for_fence = 0
    try:
        # If a comm-error flag was already set before this client connected,
        # mimic a dead device: accept and immediately close.
        if brain.comm_error_active:
            log.info("comm_error active → refusing new connection %s", addr)
            return
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            if brain.comm_error_active:
                log.info("comm_error tripped during stream → dropping %s", addr)
                break

            for event, payload in scanner.feed(chunk):
                if event == _EscPosScanner.PRINT:
                    before = brain.paper_lines
                    brain.feed_data(payload)
                    lines_pending_for_fence += before - brain.paper_lines
                elif event == _EscPosScanner.DLE_EOT:
                    status = brain.read_status_byte(payload)
                    writer.write(bytes([status]))
                    await writer.drain()
                elif event == _EscPosScanner.GS_R:
                    if lines_pending_for_fence and _PRINT_DELAY_MS_PER_LINE > 0:
                        await asyncio.sleep(
                            lines_pending_for_fence * _PRINT_DELAY_MS_PER_LINE / 1000.0
                        )
                    lines_pending_for_fence = 0
                    n_reg = payload
                    status = brain.read_status_byte(4 if n_reg in (1, 49) else n_reg)
                    writer.write(bytes([status]))
                    await writer.drain()
                    _pty_state["fence_responses"] += 1

            if lines_pending_for_fence:
                log.info("TCP chunk done — %d line(s) queued for next fence; paper %d",
                         lines_pending_for_fence, brain.paper_lines)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError) as exc:
        log.warning("printer-tcp %s closed: %s", addr, exc)
    finally:
        active_writers.discard(writer)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        log.info("printer-tcp closed %s", addr)


async def drop_all_connections() -> None:
    """Force-close every active TCP client. Used when comm_error trips."""
    for w in list(active_writers):
        try:
            w.close()
        except Exception:
            pass
    active_writers.clear()


# ---------- PTY (virtual USB-CDC) bridge ----------

# PTY-side counter (one client at a time → safe as module-global).
_pty_lines_pending_for_fence: int = 0


async def _pty_fence_response(master_fd: int, lines: int, register_n: int) -> None:
    """Async helper: sleep proportional to lines just printed, then send the
    GS r 1 response byte. Mirrors the timing of a real Cashino draining its
    receive buffer onto paper.
    """
    import os
    delay = lines * _PRINT_DELAY_MS_PER_LINE / 1000.0
    if delay > 0:
        await asyncio.sleep(delay)
    status = brain.read_status_byte(4 if register_n in (1, 49) else register_n)
    try:
        os.write(master_fd, bytes([status]))
        _pty_state["bytes_out"] += 1
        _pty_state["fence_responses"] += 1
    except OSError as exc:
        log.warning("pty fence response write failed: %s", exc)


_pty_scanner = _EscPosScanner()


def _consume_pty_buffer(master_fd: int, buf: bytearray) -> None:
    """Process bytes received over the PTY exactly like the TCP path.

    DLE EOT n queries get inline 1-byte replies (real-time);
    GS r n queries get an async-scheduled 1-byte reply (buffered fence
    — delayed by the simulated print time of preceding LFs).
    """
    import os
    global _pty_lines_pending_for_fence

    def _feed_print_segment(seg: bytes) -> int:
        if not seg:
            return 0
        before = brain.paper_lines
        brain.feed_data(seg)
        _pty_state["print_bytes"] += len(seg)
        consumed_lines = before - brain.paper_lines
        if consumed_lines:
            _pty_state["prints_received"] += 1
            log.info("PTY fed %d bytes, %d line(s) → paper %d",
                     len(seg), consumed_lines, brain.paper_lines)
        return consumed_lines

    for event, payload in _pty_scanner.feed(bytes(buf)):
        if event == _EscPosScanner.PRINT:
            _pty_lines_pending_for_fence += _feed_print_segment(payload)
        elif event == _EscPosScanner.DLE_EOT:
            status = brain.read_status_byte(payload)
            try:
                os.write(master_fd, bytes([status]))
                _pty_state["bytes_out"] += 1
                _pty_state["status_queries"] += 1
            except OSError as exc:
                log.warning("pty write status failed: %s", exc)
        elif event == _EscPosScanner.GS_R:
            lines = _pty_lines_pending_for_fence
            _pty_lines_pending_for_fence = 0
            asyncio.create_task(_pty_fence_response(master_fd, lines, payload))
    buf.clear()


async def _open_pty_bridge(symlink_path: str | None) -> None:
    """Open a pseudo-terminal, expose it as a USB-CDC-like device path.

    Idempotent: called once at startup and again whenever comm_error clears.
    """
    try:
        import fcntl
        import os
        import pty
        import tty
    except ImportError:
        log.warning("pty bridge unavailable on this platform — skipped")
        return

    if _pty_state["enabled"]:
        return  # already open

    master, slave = pty.openpty()
    try:
        tty.setraw(slave)
    except Exception:                                            # noqa: BLE001
        pass

    slave_path = os.ttyname(slave)
    flags = fcntl.fcntl(master, fcntl.F_GETFL)
    fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    _pty_state["enabled"] = True
    _pty_state["path"] = slave_path
    _pty_state["master_fd"] = master
    _pty_state["slave_fd"] = slave

    if symlink_path:
        _pty_state["intended_symlink"] = symlink_path
        try:
            if os.path.lexists(symlink_path):
                os.remove(symlink_path)
            os.symlink(slave_path, symlink_path)
            _pty_state["symlink"] = symlink_path
            log.info("virtual USB-CDC device: %s → %s", symlink_path, slave_path)
        except OSError as exc:
            log.warning("symlink %s failed: %s", symlink_path, exc)
            _pty_state["symlink"] = None
    else:
        log.info("virtual USB-CDC device at %s (no symlink)", slave_path)

    loop = asyncio.get_event_loop()
    buf = bytearray()

    def on_readable() -> None:
        try:
            chunk = os.read(master, 4096)
        except BlockingIOError:
            return
        except OSError as exc:
            log.warning("pty read error: %s", exc)
            return
        if not chunk:
            return
        _pty_state["bytes_in"] += len(chunk)
        buf.extend(chunk)
        _consume_pty_buffer(master, buf)

    loop.add_reader(master, on_readable)


async def _close_pty_bridge() -> None:
    """Tear down the PTY — modeling a yanked USB cable.

    Closing master makes the consumer's pyserial reads/writes fail
    immediately. The symlink may dangle until the PTY is reopened, which
    is exactly what we want — fresh connects fail too, matching reality.
    """
    if not _pty_state["enabled"]:
        return
    import os
    loop = asyncio.get_event_loop()
    fd = _pty_state["master_fd"]
    if fd is not None:
        try:
            loop.remove_reader(fd)
        except (ValueError, OSError):
            pass
        try:
            os.close(fd)
        except OSError:
            pass
    sfd = _pty_state["slave_fd"]
    if sfd is not None:
        try:
            os.close(sfd)
        except OSError:
            pass
    _pty_state["master_fd"] = None
    _pty_state["slave_fd"] = None
    _pty_state["enabled"] = False
    _pty_state["path"] = None
    # Keep symlink path in state for the next open; the file itself is dangling.
    log.info("virtual USB-CDC device closed (cable yanked)")


# ---------- HTTP control API (9101) ----------

class SetPaperReq(BaseModel):
    lines: int = Field(..., ge=0, le=10_000)

class SetCoverReq(BaseModel):
    open: bool

class SetJammedReq(BaseModel):
    jammed: bool

class SetTempReq(BaseModel):
    celsius: float = Field(..., ge=-20.0, le=120.0)

class CommErrReq(BaseModel):
    active: bool

class ScenarioReq(BaseModel):
    scenario: Literal["paper_out", "cover_open", "jam", "overheat",
                      "comm_drop", "recover_all"]
    auto_recover_after_ms: int | None = Field(default=None, ge=100, le=120_000)


class SetDeviceTypeReq(BaseModel):
    device_type: Literal["KP-300", "KP-301H", "KP-302"]


http_app = FastAPI(title="Mock Thermal Device")
http_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _state_snapshot() -> dict:
    p = DEVICE_PROFILES[_current_profile_name]
    return {
        "paper_lines": brain.paper_lines,
        "paper_low": brain.paper_low(),
        "temperature_c": round(brain.temperature(), 2),
        "overheated": brain.overheated(),
        "cover_open": brain.cover_open,
        "jammed": brain.jammed,
        "comm_error_active": brain.comm_error_active,
        "active_connections": len(active_writers),
        "device_profile": {
            "name": _current_profile_name,
            "label": p["label"],
            "paper_width_mm": p["paper_width_mm"],
            "paper_width_cols": p["paper_width_cols"],
            "overheat_stop_c": p["overheat_stop_c"],
            "overheat_resume_c": p["overheat_resume_c"],
            "heat_rate": p["heat_rate"],
            "cool_rate": p["cool_rate"],
        },
        "pty": {
            "enabled": _pty_state["enabled"],
            "path": _pty_state["path"],
            "symlink": _pty_state["symlink"],
            "bytes_in": _pty_state["bytes_in"],
            "bytes_out": _pty_state["bytes_out"],
            "status_queries": _pty_state["status_queries"],
            "print_bytes": _pty_state["print_bytes"],
            "prints_received": _pty_state["prints_received"],
            "fence_responses": _pty_state["fence_responses"],
        },
    }


@http_app.get("/health")
async def health():
    return {
        "ok": True,
        "service": "mock-thermal-device",
        "tcp_port": _tcp_port,
        "http_port": _http_port,
        "state": _state_snapshot(),
    }


@http_app.get("/mock/state")
async def state():
    return _state_snapshot()


@http_app.post("/mock/set_paper")
async def set_paper(req: SetPaperReq):
    brain.set_paper(req.lines)
    log.info("set_paper → %d", brain.paper_lines)
    return {"ok": True, "paper_lines": brain.paper_lines}


@http_app.post("/mock/set_cover")
async def set_cover(req: SetCoverReq):
    brain.set_cover(req.open)
    log.info("set_cover → open=%s", brain.cover_open)
    return {"ok": True, "cover_open": brain.cover_open}


@http_app.post("/mock/set_jammed")
async def set_jammed(req: SetJammedReq):
    brain.set_jammed(req.jammed)
    log.info("set_jammed → %s", brain.jammed)
    return {"ok": True, "jammed": brain.jammed}


@http_app.post("/mock/set_temperature")
async def set_temperature(req: SetTempReq):
    brain.set_temperature(req.celsius)
    log.info("set_temperature → %.1f°C overheated=%s",
             brain.temperature(), brain.overheated())
    return {"ok": True, "temperature_c": brain.temperature(),
            "overheated": brain.overheated()}


@http_app.post("/mock/trigger_comm_error")
async def trigger_comm_error(req: CommErrReq):
    brain.set_comm_error(req.active)
    log.info("trigger_comm_error → %s", brain.comm_error_active)
    if req.active:
        # Physically disconnect everything — like yanking USB + Ethernet cables.
        await drop_all_connections()
        await _close_pty_bridge()
    else:
        # Re-plug the virtual cables.
        if _pty_state["intended_symlink"]:
            await _open_pty_bridge(_pty_state["intended_symlink"])
    return {"ok": True, "comm_error_active": brain.comm_error_active}


@http_app.post("/mock/run_scenario")
async def run_scenario(req: ScenarioReq):
    s = req.scenario
    if s == "paper_out":
        brain.set_paper(0)
    elif s == "cover_open":
        brain.set_cover(True)
    elif s == "jam":
        brain.set_jammed(True)
    elif s == "overheat":
        brain.set_temperature(70.0)
    elif s == "comm_drop":
        brain.set_comm_error(True)
        await drop_all_connections()
        await _close_pty_bridge()
    elif s == "recover_all":
        brain.set_paper(500)
        brain.set_cover(False)
        brain.set_jammed(False)
        brain.set_temperature(25.0)
        brain.set_comm_error(False)
        if _pty_state["intended_symlink"]:
            await _open_pty_bridge(_pty_state["intended_symlink"])
    log.info("scenario → %s", s)

    if req.auto_recover_after_ms and s != "recover_all":
        delay = req.auto_recover_after_ms / 1000.0

        async def _recover():
            await asyncio.sleep(delay)
            if s == "paper_out": brain.set_paper(500)
            elif s == "cover_open": brain.set_cover(False)
            elif s == "jam": brain.set_jammed(False)
            elif s == "overheat": brain.set_temperature(25.0)
            elif s == "comm_drop":
                brain.set_comm_error(False)
                if _pty_state["intended_symlink"]:
                    await _open_pty_bridge(_pty_state["intended_symlink"])
            log.info("auto-recovered from %s", s)

        asyncio.create_task(_recover())

    return {"ok": True, "scenario": s}


@http_app.post("/mock/reset")
async def reset():
    p = DEVICE_PROFILES[_current_profile_name]
    brain.set_paper(p["paper_initial_lines"])
    brain.set_cover(False)
    brain.set_jammed(False)
    brain.set_temperature(25.0)
    brain.set_comm_error(False)
    log.info("reset")
    return {"ok": True}


@http_app.get("/mock/device_profiles")
async def list_profiles():
    """Catalog of supported device profiles for the UI dropdown."""
    return {
        "current": _current_profile_name,
        "profiles": [
            {"name": k, **v} for k, v in DEVICE_PROFILES.items()
        ],
    }


@http_app.post("/mock/set_device_type")
async def set_device_type(req: SetDeviceTypeReq):
    applied = _apply_profile(req.device_type)
    return {"ok": True, "device_type": req.device_type, "profile": applied}


@http_app.get("/", response_class=HTMLResponse)
async def dashboard():
    return _DASHBOARD_HTML


_DASHBOARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title data-i18n="page_title">Mock Thermal Device</title>
<style>
:root {
  --bg:#0f1115; --panel:#161a22; --panel-2:#1e242f; --border:#2a313d;
  --text:#e6e8eb; --muted:#8b94a3; --accent:#4ea0ff;
  --ok:#41c47a; --warn:#f5c560; --err:#ef6262;
}
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--text); margin:0;
       font-family:-apple-system,Segoe UI,sans-serif; font-size:14px; }
header { padding:16px 24px; border-bottom:1px solid var(--border); background:var(--panel); position:relative; }
header h1 { margin:0; font-size:18px; font-weight:600; }
header .sub { color:var(--muted); margin-left:8px; font-weight:400; }
.lang-picker { position:absolute; top:14px; right:24px; }
.lang-picker label { color:var(--muted); font-size:12px; display:inline-flex; align-items:center; gap:4px; min-width:auto; }
.lang-picker select { background:var(--panel-2); color:var(--text); border:1px solid var(--border);
  border-radius:4px; padding:2px 6px; font-size:12px; }
main { max-width:1100px; margin:0 auto; padding:18px 24px; display:grid;
       grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width: 820px) { main { grid-template-columns:1fr; } }
.card { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:16px; }
.card h2 { margin:0 0 10px; font-size:15px; font-weight:600; display:flex; align-items:center; gap:8px; }
.row { display:flex; gap:10px; align-items:center; margin:8px 0; flex-wrap:wrap; }
.row label { color:var(--muted); font-size:12px; min-width:140px; }
input[type="range"] { flex:1; min-width:160px; accent-color:var(--accent); }
input[type="number"], select, input[type="text"] {
  background:var(--panel-2); color:var(--text); border:1px solid var(--border);
  border-radius:4px; padding:5px 8px; font-size:13px; font-family:inherit;
}
button { background:var(--panel-2); color:var(--text); border:1px solid var(--border);
         border-radius:4px; padding:6px 14px; font-size:13px; cursor:pointer; }
button.primary { background:var(--accent); color:#0a1220; border-color:var(--accent); font-weight:600; }
button.danger  { background:rgba(239,98,98,0.15); border-color:var(--err); color:var(--err); }
button.ok      { background:rgba(65,196,122,0.15); border-color:var(--ok); color:var(--ok); }
button:hover { filter:brightness(1.1); }
.chip { padding:2px 10px; border-radius:12px; background:var(--panel-2);
        border:1px solid var(--border); font-size:11px; font-family:ui-monospace; }
.chip.ok   { background:rgba(65,196,122,0.15); color:var(--ok);  border-color:var(--ok); }
.chip.warn { background:rgba(245,197,96,0.15); color:var(--warn);border-color:var(--warn);}
.chip.err  { background:rgba(239,98,98,0.15); color:var(--err);  border-color:var(--err); }
.chip.muted{ background:var(--panel-2); color:var(--muted); border-color:var(--border); }
.value { font-family:ui-monospace,Menlo; color:var(--text); min-width:64px; text-align:right; }
dl.kv { display:grid; grid-template-columns:160px 1fr; gap:4px 12px; margin:0; font-size:13px; }
dl.kv dt { color:var(--muted); }
dl.kv dd { margin:0; font-family:ui-monospace,Menlo; }
.hint { color:var(--muted); font-size:12px; margin:4px 0; }
code { background:var(--panel-2); padding:2px 6px; border-radius:3px; font-size:11.5px; }
.toast { position:fixed; bottom:18px; right:18px; padding:10px 14px; border-radius:6px;
         background:var(--panel-2); border:1px solid var(--border); font-size:12px;
         opacity:0; transition:opacity .15s; }
.toast.show { opacity:1; }
.toast.ok  { border-color:var(--ok); color:var(--ok); }
.toast.err { border-color:var(--err); color:var(--err); }
</style></head>
<body>
<header>
  <h1><span data-i18n="title">Mock Thermal Device</span> <span class="sub" data-i18n="subtitle">stateful simulator (Cashino-class)</span>
    <span id="livechip" class="chip ok" style="margin-left:8px;" data-i18n="state_running">RUNNING</span></h1>
  <div class="lang-picker">
    <label>
      &#127760;
      <select id="langPicker">
        <option value="en">English</option>
        <option value="tr">Türkçe</option>
      </select>
    </label>
  </div>
</header>

<main>
  <!-- LEFT: Live state -->
  <div class="card">
    <h2><span data-i18n="live_state">Live state</span> <span id="connchip" class="chip">0 client</span></h2>
    <dl class="kv">
      <dt data-i18n="kv_paper_lines">Paper lines</dt><dd id="kPaper">—</dd>
      <dt data-i18n="kv_paper_low">Paper low?</dt><dd id="kPaperLow">—</dd>
      <dt data-i18n="kv_temp">Temperature</dt><dd id="kTemp">—</dd>
      <dt data-i18n="kv_overheated">Overheated</dt><dd id="kOver">—</dd>
      <dt data-i18n="kv_cover">Cover</dt><dd id="kCover">—</dd>
      <dt data-i18n="kv_jammed">Jammed</dt><dd id="kJam">—</dd>
      <dt data-i18n="kv_comm_error">Comm error</dt><dd id="kComm">—</dd>
      <dt data-i18n="kv_tcp_clients">TCP clients</dt><dd id="kConns">—</dd>
    </dl>

    <h2 style="margin-top:18px;"><span data-i18n="device_profile">Device profile</span> <span id="profileChip" class="chip">…</span></h2>
    <div class="row">
      <label data-i18n="profile_model">Model</label>
      <select id="profileSel" style="flex:1;">
        <option value="KP-300">KP-300</option>
        <option value="KP-301H">KP-301H</option>
        <option value="KP-302" selected>KP-302</option>
      </select>
      <button id="applyProfileBtn" class="primary" data-i18n="btn_apply">Apply</button>
    </div>
    <dl class="kv" style="margin-top:8px; font-size:12px;">
      <dt data-i18n="profile_paper_width">Paper width</dt><dd id="pfWidth">—</dd>
      <dt data-i18n="profile_cols">Columns</dt><dd id="pfCols">—</dd>
      <dt data-i18n="profile_overheat_stop">Overheat stop</dt><dd id="pfStop">—</dd>
      <dt data-i18n="profile_overheat_resume">Overheat resume</dt><dd id="pfResume">—</dd>
      <dt data-i18n="profile_heat_rate">Heat rate</dt><dd id="pfHeat">—</dd>
      <dt data-i18n="profile_cool_rate">Cool rate</dt><dd id="pfCool">—</dd>
    </dl>

    <h2 style="margin-top:18px;"><span data-i18n="virtual_usb">Virtual USB</span> <span id="ptyChip" class="chip">…</span></h2>
    <dl class="kv" style="font-size:12px;">
      <dt data-i18n="pty_path">USB-CDC path</dt><dd id="ptyPath">—</dd>
      <dt data-i18n="pty_prints">Prints received</dt><dd id="ptyPrints">0</dd>
      <dt data-i18n="pty_print_bytes">Print bytes</dt><dd id="ptyPrintBytes">0</dd>
      <dt data-i18n="pty_status_q">Status queries</dt><dd id="ptyStatusQ">0</dd>
      <dt data-i18n="pty_in">Total bytes in</dt><dd id="ptyIn">0</dd>
      <dt data-i18n="pty_out">Total bytes out</dt><dd id="ptyOut">0</dd>
    </dl>
    <p class="hint" data-i18n-html="hint_usb">Set <code>USB_DEVICE_PATH=&lt;path&gt;</code> in the main service <code>.env</code> and select <b>USB</b> mode — bytes flow over a pseudo-terminal exactly like a real USB-CDC printer.</p>

    <p class="hint" style="margin-top:14px;" data-i18n-html="hint_ports">
      TCP printer port: <code>9100</code> &nbsp;·&nbsp; Control API: <code>http://127.0.0.1:9101/mock/*</code>
    </p>
    <p class="hint" data-i18n-html="hint_drive">
      Drive me from the main service UI by setting
      <code>MOCK_DEVICE_CONTROL_URL=http://127.0.0.1:9101</code> in its <code>.env</code>.
    </p>
  </div>

  <!-- RIGHT: Controls -->
  <div class="card">
    <h2 data-i18n="controls">Controls</h2>

    <div class="row">
      <label data-i18n="ctrl_paper_lines">Paper lines</label>
      <input id="paperSlider" type="range" min="0" max="1000" step="1" value="500">
      <span id="paperVal" class="value">500</span>
      <button data-action="set_paper" class="primary" data-i18n="btn_apply">Apply</button>
    </div>

    <div class="row">
      <label data-i18n="ctrl_temp">Temperature °C</label>
      <input id="tempSlider" type="range" min="20" max="90" step="0.5" value="25">
      <span id="tempVal" class="value">25.0</span>
      <button data-action="set_temperature" class="primary" data-i18n="btn_apply">Apply</button>
    </div>

    <div class="row">
      <label data-i18n="ctrl_cover_open">Cover open</label>
      <select id="coverSel">
        <option value="false" data-i18n="val_closed">closed</option>
        <option value="true" data-i18n="val_open">open</option>
      </select>
      <button data-action="set_cover" class="primary" data-i18n="btn_apply">Apply</button>
    </div>

    <div class="row">
      <label data-i18n="ctrl_paper_jammed">Paper jammed</label>
      <select id="jamSel">
        <option value="false" data-i18n="val_no">no</option>
        <option value="true" data-i18n="val_yes">yes</option>
      </select>
      <button data-action="set_jammed" class="primary" data-i18n="btn_apply">Apply</button>
    </div>

    <div class="row">
      <label data-i18n="ctrl_comm_error">Comm error</label>
      <select id="commSel">
        <option value="false" data-i18n="val_off">off</option>
        <option value="true" data-i18n="val_on_drops">on (drops sockets)</option>
      </select>
      <button data-action="trigger_comm_error" class="primary" data-i18n="btn_apply">Apply</button>
    </div>

    <h2 style="margin-top:18px;" data-i18n="scenario_presets">Scenario presets</h2>
    <div class="row">
      <select id="scenarioSel" style="flex:1;">
        <option value="paper_out" data-i18n="sc_paper_out">Paper out</option>
        <option value="cover_open" data-i18n="sc_cover_open">Cover open</option>
        <option value="jam" data-i18n="sc_jam">Jam</option>
        <option value="overheat" data-i18n="sc_overheat">Overheat (70°C)</option>
        <option value="comm_drop" data-i18n="sc_comm_drop">Comm drop</option>
        <option value="recover_all" data-i18n="sc_recover_all">Recover all</option>
      </select>
      <label style="min-width:auto;" data-i18n="auto_recover_ms">auto-recover ms</label>
      <input id="autoRecoverMs" type="number" placeholder="off" data-i18n-placeholder="val_off" min="100" max="60000" style="width:100px;">
      <button id="runScenarioBtn" class="primary" data-i18n="btn_run">Run</button>
    </div>

    <div class="row" style="margin-top:14px;">
      <button id="resetBtn" class="ok" data-i18n="btn_reset">Reset to defaults</button>
      <button id="dropConnsBtn" class="danger" data-i18n="btn_drop_clients">Drop active TCP clients (force comm error)</button>
    </div>
  </div>
</main>

<div id="toast" class="toast"></div>

<script>
// ----- i18n -----
const I18N = {
  en: {
    page_title:           "Mock Thermal Device",
    title:                "Mock Thermal Device",
    subtitle:             "stateful simulator (Cashino-class)",
    state_running:        "RUNNING",
    state_fault:          "FAULT",
    state_comm_error:     "COMM ERROR",
    state_offline:        "OFFLINE",
    live_state:           "Live state",
    kv_paper_lines:       "Paper lines",
    kv_paper_low:         "Paper low?",
    kv_temp:              "Temperature",
    kv_overheated:        "Overheated",
    kv_cover:             "Cover",
    kv_jammed:            "Jammed",
    kv_comm_error:        "Comm error",
    kv_tcp_clients:       "TCP clients",
    device_profile:       "Device profile",
    profile_model:        "Model",
    profile_paper_width:  "Paper width",
    profile_cols:         "Columns",
    profile_overheat_stop:"Overheat stop",
    profile_overheat_resume:"Overheat resume",
    profile_heat_rate:    "Heat rate",
    profile_cool_rate:    "Cool rate",
    virtual_usb:          "Virtual USB",
    pty_enabled:          "ENABLED",
    pty_disabled:         "DISABLED",
    pty_path:             "USB-CDC path",
    pty_prints:           "Prints received",
    pty_print_bytes:      "Print bytes",
    pty_status_q:         "Status queries",
    pty_in:               "Total bytes in",
    pty_out:              "Total bytes out",
    hint_usb:             "Set <code>USB_DEVICE_PATH=&lt;path&gt;</code> in the main service <code>.env</code> and select <b>USB</b> mode — bytes flow over a pseudo-terminal exactly like a real USB-CDC printer.",
    hint_ports:           "TCP printer port: <code>9100</code> &nbsp;·&nbsp; Control API: <code>http://127.0.0.1:9101/mock/*</code>",
    hint_drive:           "Drive me from the main service UI by setting <code>MOCK_DEVICE_CONTROL_URL=http://127.0.0.1:9101</code> in its <code>.env</code>.",
    controls:             "Controls",
    ctrl_paper_lines:     "Paper lines",
    ctrl_temp:            "Temperature °C",
    ctrl_cover_open:      "Cover open",
    ctrl_paper_jammed:    "Paper jammed",
    ctrl_comm_error:      "Comm error",
    val_closed:           "closed",
    val_open:             "open",
    val_yes:              "yes",
    val_no:               "no",
    val_on:               "on",
    val_off:              "off",
    val_on_drops:         "on (drops sockets)",
    scenario_presets:     "Scenario presets",
    sc_paper_out:         "Paper out",
    sc_cover_open:        "Cover open",
    sc_jam:               "Jam",
    sc_overheat:          "Overheat (70°C)",
    sc_comm_drop:         "Comm drop",
    sc_recover_all:       "Recover all",
    auto_recover_ms:      "auto-recover ms",
    btn_apply:            "Apply",
    btn_run:              "Run",
    btn_reset:            "Reset to defaults",
    btn_drop_clients:     "Drop active TCP clients (force comm error)",
    client_one:           "client",
    client_many:          "clients",
    toast_dropping:       "dropping clients…",
  },
  tr: {
    page_title:           "Mock Termal Cihaz",
    title:                "Mock Termal Cihaz",
    subtitle:             "durumlu simülatör (Cashino sınıfı)",
    state_running:        "ÇALIŞIYOR",
    state_fault:          "ARIZA",
    state_comm_error:     "İLETİŞİM HATASI",
    state_offline:        "KAPALI",
    live_state:           "Canlı durum",
    kv_paper_lines:       "Kağıt satırı",
    kv_paper_low:         "Kağıt az mı?",
    kv_temp:              "Sıcaklık",
    kv_overheated:        "Aşırı ısındı",
    kv_cover:             "Kapak",
    kv_jammed:            "Sıkışma",
    kv_comm_error:        "İletişim hatası",
    kv_tcp_clients:       "TCP istemci",
    device_profile:       "Cihaz profili",
    profile_model:        "Model",
    profile_paper_width:  "Kağıt genişliği",
    profile_cols:         "Sütun",
    profile_overheat_stop:"Aşırı ısı durdurma",
    profile_overheat_resume:"Aşırı ısı devam",
    profile_heat_rate:    "Isınma oranı",
    profile_cool_rate:    "Soğuma oranı",
    virtual_usb:          "Sanal USB",
    pty_enabled:          "AÇIK",
    pty_disabled:         "KAPALI",
    pty_path:             "USB-CDC yolu",
    pty_prints:           "Alınan baskı",
    pty_print_bytes:      "Baskı byte",
    pty_status_q:         "Durum sorgusu",
    pty_in:               "Toplam gelen byte",
    pty_out:              "Toplam giden byte",
    hint_usb:             "Ana servisin <code>.env</code> dosyasında <code>USB_DEVICE_PATH=&lt;yol&gt;</code> ayarlayıp <b>USB</b> modunu seçin — byte'lar gerçek bir USB-CDC yazıcı gibi pseudo-terminal üzerinden akar.",
    hint_ports:           "TCP yazıcı portu: <code>9100</code> &nbsp;·&nbsp; Kontrol API: <code>http://127.0.0.1:9101/mock/*</code>",
    hint_drive:           "Ana servis UI'sından kullanmak için onun <code>.env</code> dosyasına <code>MOCK_DEVICE_CONTROL_URL=http://127.0.0.1:9101</code> ekleyin.",
    controls:             "Kontroller",
    ctrl_paper_lines:     "Kağıt satırı",
    ctrl_temp:            "Sıcaklık °C",
    ctrl_cover_open:      "Kapak açık",
    ctrl_paper_jammed:    "Kağıt sıkışık",
    ctrl_comm_error:      "İletişim hatası",
    val_closed:           "kapalı",
    val_open:             "açık",
    val_yes:              "evet",
    val_no:               "hayır",
    val_on:               "açık",
    val_off:              "kapalı",
    val_on_drops:         "açık (bağlantıyı düşürür)",
    scenario_presets:     "Senaryo şablonları",
    sc_paper_out:         "Kağıt bitti",
    sc_cover_open:        "Kapak açık",
    sc_jam:               "Sıkışma",
    sc_overheat:          "Aşırı ısınma (70°C)",
    sc_comm_drop:         "Bağlantı koptu",
    sc_recover_all:       "Hepsini düzelt",
    auto_recover_ms:      "otomatik düzelt ms",
    btn_apply:            "Uygula",
    btn_run:              "Çalıştır",
    btn_reset:            "Varsayılana dön",
    btn_drop_clients:     "Aktif TCP istemcilerini düşür (iletişim hatası zorla)",
    client_one:           "istemci",
    client_many:          "istemci",
    toast_dropping:       "istemciler düşürülüyor…",
  },
};
function t(key) {
  const lang = localStorage.getItem("ui_lang") || "en";
  return (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
}
function applyLanguage(lang) {
  if (!I18N[lang]) lang = "en";
  const dict = I18N[lang];
  document.documentElement.lang = lang;
  for (const el of document.querySelectorAll("[data-i18n]")) {
    const v = dict[el.dataset.i18n]; if (v != null) el.textContent = v;
  }
  for (const el of document.querySelectorAll("[data-i18n-html]")) {
    const v = dict[el.dataset.i18nHtml]; if (v != null) el.innerHTML = v;
  }
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    const v = dict[el.dataset.i18nPlaceholder]; if (v != null) el.placeholder = v;
  }
  document.title = dict.page_title || document.title;
  localStorage.setItem("ui_lang", lang);
  if (typeof refresh === "function") refresh();
}

const $ = id => document.getElementById(id);
const toast = (msg, kind) => {
  const t = $('toast'); t.textContent = msg;
  t.className = 'toast show ' + (kind || '');
  setTimeout(() => t.classList.remove('show'), 1800);
};
async function call(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  let payload = null;
  try { payload = await r.json(); } catch {}
  return { ok: r.ok, status: r.status, body: payload };
}

async function refresh() {
  try {
    const r = await fetch('/mock/state');
    if (!r.ok) throw new Error('state ' + r.status);
    const s = await r.json();
    $('kPaper').textContent = s.paper_lines;
    $('kPaperLow').textContent = s.paper_low ? t('val_yes') : t('val_no');
    $('kTemp').textContent = s.temperature_c.toFixed(1) + ' °C';
    $('kOver').textContent = s.overheated ? t('val_yes').toUpperCase() : t('val_no');
    $('kCover').textContent = s.cover_open ? t('val_open') : t('val_closed');
    $('kJam').textContent = s.jammed ? t('val_yes').toUpperCase() : t('val_no');
    $('kComm').textContent = s.comm_error_active ? t('val_on').toUpperCase() : t('val_off');
    $('kConns').textContent = s.active_connections;
    $('connchip').className = 'chip ' + (s.active_connections > 0 ? 'ok' : '');
    const w = s.active_connections === 1 ? t('client_one') : t('client_many');
    $('connchip').textContent = s.active_connections + ' ' + w;

    // Device profile panel
    if (s.device_profile) {
      const p = s.device_profile;
      $('profileChip').textContent = p.name;
      $('profileChip').className = 'chip ok';
      $('pfWidth').textContent = p.paper_width_mm + ' mm';
      $('pfCols').textContent = p.paper_width_cols + ' chars';
      $('pfStop').textContent = p.overheat_stop_c + ' °C';
      $('pfResume').textContent = p.overheat_resume_c + ' °C';
      $('pfHeat').textContent = p.heat_rate + ' °C/s';
      $('pfCool').textContent = p.cool_rate + ' °C/s';
      // Sync dropdown to current value without firing change handlers
      if ($('profileSel').value !== p.name) $('profileSel').value = p.name;
    }

    // PTY (virtual USB) panel
    if (s.pty) {
      const pty = s.pty;
      if (pty.enabled) {
        $('ptyChip').textContent = t('pty_enabled');
        $('ptyChip').className = 'chip ok';
        $('ptyPath').textContent = pty.symlink ? (pty.symlink + '  →  ' + pty.path) : pty.path;
      } else {
        $('ptyChip').textContent = t('pty_disabled');
        $('ptyChip').className = 'chip muted';
        $('ptyPath').textContent = '—';
      }
      $('ptyPrints').textContent = pty.prints_received ?? 0;
      $('ptyPrintBytes').textContent = pty.print_bytes ?? 0;
      $('ptyStatusQ').textContent = pty.status_queries ?? 0;
      $('ptyIn').textContent = pty.bytes_in;
      $('ptyOut').textContent = pty.bytes_out;
    }

    // chip colors reflect fault state
    const live = $('livechip');
    if (s.comm_error_active) { live.className = 'chip err';  live.textContent = t('state_comm_error'); }
    else if (s.paper_lines === 0 || s.cover_open || s.jammed || s.overheated) {
      live.className = 'chip warn'; live.textContent = t('state_fault');
    } else { live.className = 'chip ok'; live.textContent = t('state_running'); }
  } catch (e) {
    $('livechip').className = 'chip err';
    $('livechip').textContent = t('state_offline');
  }
}

$('applyProfileBtn').addEventListener('click', async () => {
  const dev = $('profileSel').value;
  const r = await call('/mock/set_device_type', { device_type: dev });
  toast(`device: ${dev} → ${r.status}`, r.ok ? 'ok' : 'err');
  refresh();
});

// Slider live readouts
$('paperSlider').addEventListener('input', e => $('paperVal').textContent = e.target.value);
$('tempSlider').addEventListener('input', e => $('tempVal').textContent = parseFloat(e.target.value).toFixed(1));

// Apply buttons
document.querySelectorAll('button[data-action]').forEach(btn => {
  btn.addEventListener('click', async () => {
    const action = btn.dataset.action;
    let body = {};
    if (action === 'set_paper') body = { lines: parseInt($('paperSlider').value, 10) };
    if (action === 'set_temperature') body = { celsius: parseFloat($('tempSlider').value) };
    if (action === 'set_cover') body = { open: $('coverSel').value === 'true' };
    if (action === 'set_jammed') body = { jammed: $('jamSel').value === 'true' };
    if (action === 'trigger_comm_error') body = { active: $('commSel').value === 'true' };
    const r = await call('/mock/' + action, body);
    toast(`${action}: ${r.status}`, r.ok ? 'ok' : 'err');
    refresh();
  });
});

$('runScenarioBtn').addEventListener('click', async () => {
  const body = { scenario: $('scenarioSel').value };
  const ms = parseInt($('autoRecoverMs').value, 10);
  if (ms) body.auto_recover_after_ms = ms;
  const r = await call('/mock/run_scenario', body);
  toast(`scenario ${body.scenario}: ${r.status}`, r.ok ? 'ok' : 'err');
  refresh();
});

$('resetBtn').addEventListener('click', async () => {
  const r = await call('/mock/reset');
  toast(`reset: ${r.status}`, r.ok ? 'ok' : 'err');
  refresh();
});

$('dropConnsBtn').addEventListener('click', async () => {
  // Equivalent to: enable comm error briefly, then disable
  await call('/mock/trigger_comm_error', { active: true });
  setTimeout(() => call('/mock/trigger_comm_error', { active: false }).then(refresh), 250);
  toast(t('toast_dropping'), 'ok');
});

// ----- Language picker init -----
(() => {
  const saved = localStorage.getItem("ui_lang");
  const browser = (navigator.language || "en").slice(0, 2).toLowerCase();
  const lang = saved || (I18N[browser] ? browser : "en");
  const picker = document.getElementById("langPicker");
  if (picker) {
    picker.value = lang;
    picker.addEventListener("change", e => applyLanguage(e.target.value));
  }
  applyLanguage(lang);
})();

// Initial + polling
refresh();
setInterval(refresh, 1500);
</script>
</body></html>"""


# ---------- Entrypoint: TCP + HTTP concurrently ----------

_tcp_port = 9100
_http_port = 9101


async def main(tcp_host: str, tcp_port: int, http_host: str, http_port: int,
               enable_pty: bool, pty_symlink: str | None) -> None:
    global _tcp_port, _http_port
    _tcp_port, _http_port = tcp_port, http_port

    tcp_server = await asyncio.start_server(handle_tcp, tcp_host, tcp_port)
    log.info("printer TCP service on %s:%d", tcp_host, tcp_port)

    if enable_pty:
        _pty_state["intended_symlink"] = pty_symlink
        await _open_pty_bridge(pty_symlink)

    config = uvicorn.Config(http_app, host=http_host, port=http_port,
                            log_level="warning", access_log=False)
    http_server = uvicorn.Server(config)
    log.info("control API on http://%s:%d  (dashboard at /)", http_host, http_port)

    async with tcp_server:
        await asyncio.gather(tcp_server.serve_forever(), http_server.serve())


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tcp-host", default="127.0.0.1",
                   help="TCP bind address (use 0.0.0.0 in Docker; default 127.0.0.1)")
    p.add_argument("--tcp-port", type=int, default=9100)
    p.add_argument("--http-host", default="127.0.0.1",
                   help="HTTP control API bind address (use 0.0.0.0 in Docker)")
    p.add_argument("--http-port", type=int, default=9101)
    p.add_argument("--enable-pty", action="store_true", default=True,
                   help="expose a USB-CDC pseudo-terminal (default on; ignored in Docker)")
    p.add_argument("--no-pty", dest="enable_pty", action="store_false",
                   help="disable the PTY bridge")
    p.add_argument("--pty-symlink", default="/tmp/mock-printer-usb",
                   help="stable path that points to the PTY (default: /tmp/mock-printer-usb)")
    p.add_argument("--print-delay-ms", type=int, default=10,
                   help="simulated per-line print time in ms (default 10, Cashino KP-302 ≈ 250mm/s)")
    args = p.parse_args()
    _PRINT_DELAY_MS_PER_LINE = max(0, args.print_delay_ms)  # noqa: F841  (module-level reassign)
    # Re-bind via globals() so helper functions reading the name see the override.
    globals()["_PRINT_DELAY_MS_PER_LINE"] = _PRINT_DELAY_MS_PER_LINE
    try:
        asyncio.run(main(args.tcp_host, args.tcp_port,
                         args.http_host, args.http_port,
                         args.enable_pty, args.pty_symlink))
    except KeyboardInterrupt:
        log.info("bye")
