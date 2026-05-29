"""Abstract DeviceTransport — the seam between service and printer.

Both MockTransport (faz 1) and RealTransport (faz 6) implement this.
The service layer never knows which is wired in.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal


ConnectionMode = Literal["usb", "lan"]


class DeviceTransport(ABC):
    """Async-only interface. All methods may raise CommError."""

    @property
    @abstractmethod
    def mode(self) -> ConnectionMode: ...

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish link. Raises CommError on failure."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down link. Should not raise."""

    @abstractmethod
    async def send(self, data: bytes) -> None:
        """Write ESC/POS bytes. Raises CommError or PrinterError on issues."""

    @abstractmethod
    async def read_status(self, register: int) -> int:
        """Issue DLE EOT n=register and return the status byte (0–255).

        register: 1 (printer), 2 (offline), 3 (error), 4 (paper sensor).
        Raises CommError on link issues.
        """

    async def await_buffer_drain(self, timeout_ms: int) -> int:
        """GS r 1 fence — block until the device finishes processing every
        byte sent so far, then return the 1-byte paper-sensor status.

        Per Cashino KP-300 manual p.63, `GS r n` is *buffered*: the response
        only arrives after the receive buffer is processed up to that point.
        We rely on that to know the physical print is complete.

        Default implementation: send `1D 72 01` then read 1 byte. Subclasses
        may override for transport-specific timing, but most use the default.
        """
        import asyncio
        loop = asyncio.get_event_loop()
        # send the fence command
        await self.send(b"\x1d\x72\x01")
        # The actual read is transport-specific — handled by read_status'
        # plumbing. We expose a thin helper because real transports need
        # different timeouts than DLE EOT (DLE EOT < 50 ms, GS r 1 up to
        # ~few seconds while paper feeds).
        raise NotImplementedError(
            "transport must override await_buffer_drain to read with custom timeout"
        )
