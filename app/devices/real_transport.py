"""Real transport implementations — LAN (raw TCP 9100) and USB (libusb).

Both honor the DeviceTransport contract so the service layer treats them
identically to MockTransport. ESC/POS byte production happens in the
renderer (with our own cp857 encoder per R1); this layer just ferries
bytes and reads DLE EOT status responses.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from typing import Literal

from app.core.errors import CommError
from app.devices.transport import ConnectionMode, DeviceTransport


log = logging.getLogger(__name__)


class LanTransport(DeviceTransport):
    """Raw TCP 9100 — the standard ESC/POS network port.

    No dependencies beyond stdlib. Works on macOS / Linux / Windows
    without permission setup.
    """

    def __init__(self, host: str, port: int = 9100, timeout: float = 2.0):
        if not host:
            raise CommError("LAN_HOST is empty — set it in .env")
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: socket.socket | None = None

    @property
    def mode(self) -> ConnectionMode:
        return "lan"

    @property
    def connected(self) -> bool:
        return self._sock is not None

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            sock = await loop.run_in_executor(
                None,
                lambda: socket.create_connection((self._host, self._port),
                                                 timeout=self._timeout),
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise CommError(
                f"LAN connect to {self._host}:{self._port} failed: {exc}"
            ) from exc
        sock.settimeout(self._timeout)
        self._sock = sock
        log.info("LAN connected",
                 extra={"op": "lan_connect", "host": self._host, "port": self._port})

    async def disconnect(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    async def send(self, data: bytes) -> None:
        if self._sock is None:
            raise CommError("LAN transport not connected")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._sock.sendall, data)
        except OSError as exc:
            self._sock = None
            raise CommError(f"LAN send failed: {exc}") from exc

    async def read_status(self, register: int) -> int:
        if self._sock is None:
            raise CommError("LAN transport not connected")
        loop = asyncio.get_event_loop()
        cmd = bytes([0x10, 0x04, register & 0xFF])    # DLE EOT n

        def _do_io() -> bytes:
            # Drain any stray bytes the printer parked on the socket before
            # this read — most commonly a spurious DLE-EOT response triggered
            # by a `10 04 nn` byte sequence that happened to occur inside the
            # raster image or QR data of the prior receipt. Without this,
            # an unrelated 0x1E (n4 paper-near-end) ends up as the answer
            # to our DLE EOT 3, which decodes as bit-2 set → false PAPER_JAM.
            self._sock.setblocking(False)
            try:
                while True:
                    try:
                        if not self._sock.recv(64):
                            break
                    except (BlockingIOError, OSError):
                        break
            finally:
                self._sock.setblocking(True)
            self._sock.sendall(cmd)
            return self._sock.recv(1)

        try:
            data = await loop.run_in_executor(None, _do_io)
        except OSError as exc:
            self._sock = None
            raise CommError(f"LAN status read failed: {exc}") from exc
        if not data:
            self._sock = None
            raise CommError(
                f"LAN no response to DLE EOT n={register} (peer closed)"
            )
        return data[0]

    async def await_buffer_drain(self, timeout_ms: int) -> int:
        """GS r 1 fence over LAN — buffered command, response gates on
        printer-buffer drain. Per Cashino KP-300 user manual, p.63.
        Returns the 1-byte paper sensor status from the response.
        """
        if self._sock is None:
            raise CommError("LAN transport not connected")
        loop = asyncio.get_event_loop()

        def _do_io() -> bytes:
            # Drain any stray bytes left over from prior DLE EOT replies so we
            # don't mistake them for the fence response (which happens to look
            # identical — 0x12 reserved-bits byte).
            self._sock.setblocking(False)
            try:
                while True:
                    try:
                        if not self._sock.recv(64):
                            break
                    except (BlockingIOError, OSError):
                        break
            finally:
                self._sock.setblocking(True)
            self._sock.sendall(b"\x1d\x72\x01")
            old = self._sock.gettimeout()
            self._sock.settimeout(timeout_ms / 1000.0)
            try:
                return self._sock.recv(1)
            finally:
                self._sock.settimeout(old)

        try:
            data = await loop.run_in_executor(None, _do_io)
        except OSError as exc:
            self._sock = None
            raise CommError(f"LAN fence read failed: {exc}") from exc
        if not data:
            self._sock = None
            raise CommError("LAN no response to GS r 1 fence (timeout?)")
        return data[0]


class UsbTransport(DeviceTransport):
    """USB transport via python-escpos / libusb.

    Tested manually against Cashino KP-302 only. On Linux requires udev
    rule (see scripts/udev/99-escpos.rules) + lp/dialout group membership.
    On Windows requires Zadig + libusb-win32 driver. On macOS works
    out-of-the-box once libusb is installed via brew.
    """

    def __init__(self, vendor_id: str, product_id: str, timeout: float = 2.0,
                 in_ep: int = 0x81, out_ep: int = 0x03):
        if not vendor_id or not product_id:
            raise CommError("USB_VENDOR_ID / USB_PRODUCT_ID must be set in .env")
        try:
            self._vendor = int(str(vendor_id), 16) if isinstance(vendor_id, str) else int(vendor_id)
            self._product = int(str(product_id), 16) if isinstance(product_id, str) else int(product_id)
        except ValueError as exc:
            raise CommError(f"USB vendor/product id must be hex (e.g. 0x0fe6): {exc}") from exc
        self._timeout_ms = int(timeout * 1000)
        self._in_ep = in_ep
        self._out_ep = out_ep
        self._device = None         # escpos.printer.Usb instance

    @property
    def mode(self) -> ConnectionMode:
        return "usb"

    @property
    def connected(self) -> bool:
        return self._device is not None

    async def connect(self) -> None:
        try:
            from escpos.printer import Usb
        except ImportError as exc:
            raise CommError(f"python-escpos not installed: {exc}") from exc

        loop = asyncio.get_event_loop()

        def _open():
            return Usb(self._vendor, self._product,
                       timeout=self._timeout_ms,
                       in_ep=self._in_ep, out_ep=self._out_ep)

        try:
            self._device = await loop.run_in_executor(None, _open)
        except Exception as exc:                                 # noqa: BLE001
            raise CommError(
                f"USB connect failed (vid={self._vendor:#06x}, pid={self._product:#06x}): {exc}"
            ) from exc
        log.info("USB connected",
                 extra={"op": "usb_connect",
                        "vendor": f"{self._vendor:#06x}",
                        "product": f"{self._product:#06x}"})

    async def disconnect(self) -> None:
        dev = self._device
        self._device = None
        if dev is not None:
            try:
                dev.close()
            except Exception:                                    # noqa: BLE001
                pass

    async def send(self, data: bytes) -> None:
        if self._device is None:
            raise CommError("USB transport not connected")
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._device._raw, data)   # noqa: SLF001
        except Exception as exc:                                 # noqa: BLE001
            self._device = None
            raise CommError(f"USB send failed: {exc}") from exc

    async def read_status(self, register: int) -> int:
        if self._device is None:
            raise CommError("USB transport not connected")
        loop = asyncio.get_event_loop()
        cmd = bytes([0x10, 0x04, register & 0xFF])

        def _do():
            self._device._raw(cmd)                               # noqa: SLF001
            raw = self._device.device.read(self._in_ep, 1, timeout=self._timeout_ms)
            return bytes(raw) if raw else b""

        try:
            data = await loop.run_in_executor(None, _do)
        except Exception as exc:                                 # noqa: BLE001
            self._device = None
            raise CommError(f"USB status read failed: {exc}") from exc
        if not data:
            self._device = None
            raise CommError(
                f"USB no response to DLE EOT n={register} (device unresponsive)"
            )
        return int(data[0])

    async def await_buffer_drain(self, timeout_ms: int) -> int:
        """GS r 1 fence over libusb — buffered command. Uses an enlarged
        IN-endpoint timeout because the printer holds the response until it
        has fed all preceding bytes onto paper.
        """
        if self._device is None:
            raise CommError("USB transport not connected")
        loop = asyncio.get_event_loop()

        def _do():
            self._device._raw(b"\x1d\x72\x01")                   # noqa: SLF001
            raw = self._device.device.read(self._in_ep, 1, timeout=timeout_ms)
            return bytes(raw) if raw else b""

        try:
            data = await loop.run_in_executor(None, _do)
        except Exception as exc:                                 # noqa: BLE001
            self._device = None
            raise CommError(f"USB fence read failed: {exc}") from exc
        if not data:
            self._device = None
            raise CommError("USB no response to GS r 1 fence (timeout?)")
        return int(data[0])
