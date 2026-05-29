"""MockTransport — connect/disconnect/send/read_status link-layer behavior."""
from __future__ import annotations

import pytest

from app.core.clock import FakeClock
from app.core.errors import CommError
from app.devices.mock_printer import MockPrinter, MockPrinterConfig
from app.devices.mock_transport import MockTransport


def make_transport(**cfg) -> tuple[MockTransport, MockPrinter]:
    clock = FakeClock()
    printer = MockPrinter(config=MockPrinterConfig(**cfg), clock=clock)
    return MockTransport(printer, mode="lan"), printer


async def test_connect_succeeds_when_healthy():
    t, _ = make_transport()
    await t.connect()
    assert t.connected is True


async def test_connect_raises_on_comm_error_flag():
    t, p = make_transport()
    p.set_comm_error(True)
    with pytest.raises(CommError):
        await t.connect()


async def test_disconnect_clears_flag():
    t, _ = make_transport()
    await t.connect()
    await t.disconnect()
    assert t.connected is False


async def test_send_requires_connection():
    t, _ = make_transport()
    with pytest.raises(CommError):
        await t.send(b"hello\n")


async def test_send_decrements_paper_via_brain():
    t, p = make_transport(paper_initial_lines=5)
    await t.connect()
    await t.send(b"a\nb\nc\n")
    assert p.paper_lines == 2


async def test_comm_error_during_send_disconnects():
    t, p = make_transport()
    await t.connect()
    p.set_comm_error(True)
    with pytest.raises(CommError):
        await t.send(b"x\n")
    assert t.connected is False


async def test_read_status_requires_connection():
    t, _ = make_transport()
    with pytest.raises(CommError):
        await t.read_status(2)


async def test_read_status_returns_brain_byte():
    t, p = make_transport()
    await t.connect()
    p.set_cover(True)
    byte = await t.read_status(2)
    assert byte & (1 << 2)


async def test_mode_property():
    t, _ = make_transport()
    assert t.mode == "lan"
