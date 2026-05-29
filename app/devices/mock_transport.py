"""MockTransport — DeviceTransport implementation wrapping a MockPrinter brain.

Translates link-layer concerns (connect/disconnect, comm errors) into
exceptions, while delegating data and status to the brain.
"""
from __future__ import annotations

from typing import Literal

from app.core.errors import CommError
from app.devices.mock_printer import MockPrinter
from app.devices.transport import ConnectionMode, DeviceTransport


class MockTransport(DeviceTransport):
    def __init__(self, printer: MockPrinter, mode: ConnectionMode = "lan"):
        self._printer = printer
        self._mode: ConnectionMode = mode
        self._connected = False

    @property
    def mode(self) -> ConnectionMode:
        return self._mode

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def printer(self) -> MockPrinter:
        """Test/control hook — direct access to the brain for /mock/* endpoints."""
        return self._printer

    async def connect(self) -> None:
        if self._printer.comm_error_active:
            raise CommError("mock comm_error flag is set")
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send(self, data: bytes) -> None:
        if not self._connected:
            raise CommError("transport not connected")
        if self._printer.comm_error_active:
            self._connected = False
            raise CommError("mock comm_error tripped during send")
        self._printer.feed_data(data)

    async def read_status(self, register: int) -> int:
        if not self._connected:
            raise CommError("transport not connected")
        if self._printer.comm_error_active:
            self._connected = False
            raise CommError("mock comm_error tripped during status read")
        return self._printer.read_status_byte(register)

    async def await_buffer_drain(self, timeout_ms: int) -> int:
        """In-process mock has no real buffer; the brain consumed each byte
        synchronously the moment `send()` returned. The fence response is
        therefore immediate. (The standalone `mock_device_server` simulates
        realistic print delay; that's where users see end-to-end blocking.)

        We still emit the `1D 72 01` bytes via the brain's feed path so any
        byte-stream consumer (preview, byte-tap tests) sees the same wire
        traffic the real transports produce.
        """
        if not self._connected:
            raise CommError("transport not connected")
        if self._printer.comm_error_active:
            self._connected = False
            raise CommError("mock comm_error tripped during fence read")
        self._printer.feed_data(b"\x1d\x72\x01")
        # GS r 1 returns paper-sensor status — same byte as DLE EOT n=4.
        return self._printer.read_status_byte(4)
