"""asyncio.start_server-based fake TCP listener for LAN-discovery tests.

The fake reads bytes; when it sees `DLE EOT n` (0x10 0x04 <n>) it
replies with a single configured byte. Anything else is ignored, which
is how a real Cashino would behave for unknown buffered bytes.
"""
from __future__ import annotations

import asyncio
import contextlib
import socket


class FakePrinterServer:
    """Tiny TCP listener that mimics a Cashino's DLE-EOT response shape.

    Usage:
        async with FakePrinterServer(reply_byte=0x12) as srv:
            host, port = srv.address
            # ...do probes against host:port...
    """

    def __init__(self, reply_byte: int | None = 0x12,
                 host: str = "127.0.0.1") -> None:
        self._reply = bytes([reply_byte]) if reply_byte is not None else b""
        self._host = host
        self._server: asyncio.base_events.Server | None = None
        self._port: int | None = None
        self._connections = 0

    @property
    def address(self) -> tuple[str, int]:
        assert self._port is not None, "server not started"
        return (self._host, self._port)

    @property
    def connection_count(self) -> int:
        return self._connections

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        self._connections += 1
        try:
            while True:
                chunk = await reader.read(64)
                if not chunk:
                    return
                # Reply to DLE EOT n with the configured byte (silent
                # server is the no-reply variant: reply_byte=None).
                for i in range(len(chunk) - 2):
                    if chunk[i] == 0x10 and chunk[i + 1] == 0x04:
                        if self._reply:
                            writer.write(self._reply)
                            await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError, OSError):
            return
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def __aenter__(self) -> "FakePrinterServer":
        # Bind to port 0 so the kernel picks a free one — avoids
        # collisions when several tests run in parallel.
        self._server = await asyncio.start_server(
            self._handle, self._host, 0
        )
        sock = self._server.sockets[0] if self._server.sockets else None
        if sock is not None:
            self._port = sock.getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


def reserve_unused_port() -> int:
    """Bind to port 0, close, return the port. Useful when a test needs
    a port that is *guaranteed-closed* so the probe sees connection
    refused."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port
