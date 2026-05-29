"""USB-CDC / serial transport.

Most Cashino USB printers register as USB-CDC ACM devices on the host OS:

    macOS:   /dev/cu.usbserial-XXXX  or  /dev/cu.usbmodemXXXX
    Linux:   /dev/ttyUSB0  or  /dev/ttyACM0
    Windows: COM3 (or higher)

This transport opens such a device via pyserial. It also lets us point at a
PTY symlink (e.g. /tmp/mock-printer-usb created by `scripts/mock_device_server.py
--enable-pty`) so the same code path can drive the mock without a physical
device.

For Cashino printers that present as a vendor-class USB device (no CDC
endpoint), use `UsbTransport` (libusb) instead.
"""
from __future__ import annotations

import asyncio
import logging

import serial

from app.core.errors import CommError
from app.devices.transport import ConnectionMode, DeviceTransport


log = logging.getLogger(__name__)


class SerialTransport(DeviceTransport):
    def __init__(self, device_path: str, baudrate: int = 115200,
                 timeout: float = 0.5):
        # 115200 default: real USB-CDC ignores baud (it's just a USB CDC field),
        # but the kernel DOES throttle PTYs at the configured rate. At 9600
        # a 500-byte receipt trickles over ~400ms, making the mock UI byte
        # counter crawl. 115200 makes PTY transfers effectively instant.
        if not device_path:
            raise CommError(
                "USB_DEVICE_PATH empty — set it in .env (or use libusb via "
                "USB_VENDOR_ID/USB_PRODUCT_ID)"
            )
        self._path = device_path
        self._baud = baudrate
        self._timeout = timeout
        self._ser: serial.Serial | None = None

    @property
    def mode(self) -> ConnectionMode:
        return "usb"

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()

        def _open():
            return serial.Serial(
                self._path,
                self._baud,
                timeout=self._timeout,
                write_timeout=self._timeout,
            )

        try:
            self._ser = await loop.run_in_executor(None, _open)
        except (serial.SerialException, OSError) as exc:
            raise CommError(
                f"USB-CDC open {self._path} failed: {exc}"
            ) from exc
        log.info("USB-CDC opened",
                 extra={"op": "usb_open", "path": self._path,
                        "baudrate": self._baud})

    async def disconnect(self) -> None:
        ser = self._ser
        self._ser = None
        if ser is not None:
            try:
                ser.close()
            except Exception:                                    # noqa: BLE001
                pass

    async def send(self, data: bytes) -> None:
        if self._ser is None:
            raise CommError("USB-CDC not connected")
        loop = asyncio.get_event_loop()

        def _write():
            self._ser.write(data)
            self._ser.flush()

        try:
            await loop.run_in_executor(None, _write)
        except (serial.SerialException, OSError) as exc:
            self._ser = None
            raise CommError(f"USB-CDC send failed: {exc}") from exc

    async def read_status(self, register: int) -> int:
        if self._ser is None:
            raise CommError("USB-CDC not connected")
        loop = asyncio.get_event_loop()
        cmd = bytes([0x10, 0x04, register & 0xFF])

        def _do_io() -> bytes:
            # Same defensive drain as the fence path — keeps a stray byte
            # (delayed fence response, prior status echo) from being mis-
            # interpreted as the answer to a *different* DLE EOT register
            # and triggering a phantom error (e.g. n4's "near-end" byte
            # 0x1E read as n3 with bit 2 set → false PAPER_JAM).
            stale = self._ser.in_waiting
            if stale:
                self._ser.read(stale)
            self._ser.write(cmd)
            self._ser.flush()
            return self._ser.read(1)

        try:
            data = await loop.run_in_executor(None, _do_io)
        except (serial.SerialException, OSError) as exc:
            self._ser = None
            raise CommError(f"USB-CDC status read failed: {exc}") from exc
        if not data:
            # Timeout with no byte — the device is unresponsive even though
            # the file is still open (e.g. cable yanked, mock comm_error).
            self._ser = None
            raise CommError(
                f"USB-CDC timeout on DLE EOT n={register} (device unresponsive)"
            )
        return data[0]

    async def await_buffer_drain(self, timeout_ms: int) -> int:
        """GS r 1 fence over USB-CDC. The fence response only arrives once the
        printer has fed every byte before it onto paper — so we temporarily
        bump pyserial's read timeout to the configured fence timeout.
        """
        if self._ser is None:
            raise CommError("USB-CDC not connected")
        loop = asyncio.get_event_loop()

        def _do_io() -> bytes:
            # Drain any stray bytes (late DLE EOT responses, OS PTY echo, etc.)
            # so the next read returns ONLY the fence response. Without this
            # we sometimes pick up a 0x12 left in the input buffer and skip
            # the wait entirely.
            stale = self._ser.in_waiting
            if stale:
                self._ser.read(stale)
            self._ser.write(b"\x1d\x72\x01")
            self._ser.flush()
            previous = self._ser.timeout
            self._ser.timeout = timeout_ms / 1000.0
            try:
                return self._ser.read(1)
            finally:
                self._ser.timeout = previous

        try:
            data = await loop.run_in_executor(None, _do_io)
        except (serial.SerialException, OSError) as exc:
            self._ser = None
            raise CommError(f"USB-CDC fence read failed: {exc}") from exc
        if not data:
            self._ser = None
            raise CommError("USB-CDC no response to GS r 1 fence (timeout?)")
        return data[0]
