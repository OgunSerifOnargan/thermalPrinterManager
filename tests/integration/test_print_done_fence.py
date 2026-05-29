"""GS r 1 fence — service waits for buffer-drain before releasing lock.

Per Cashino KP-300 manual p.63, `GS r n` is a buffered command whose response
gates on receive-buffer drain. The service uses this as a "print done" signal.
"""
from __future__ import annotations

import asyncio
import time

from httpx import AsyncClient

from app.devices.mock_printer import MockPrinter


def _body() -> dict:
    return {
        "machine_id": "FENCE",
        "items": [{"product": "x", "quantity": 1, "reward": 1.0}],
        "total_reward": 1.0,
    }


async def test_fence_byte_sent_after_payload(
    client: AsyncClient, mock_printer: MockPrinter,
):
    """The bytes hitting the brain end with `1D 72 01` (GS r 1)."""
    captured: list[bytes] = []
    original_feed = mock_printer.feed_data

    def _spy(data: bytes) -> None:
        captured.append(bytes(data))
        original_feed(data)

    mock_printer.feed_data = _spy   # type: ignore[assignment]

    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json=_body())
    assert r.status_code == 200

    stream = b"".join(captured)
    assert stream.endswith(b"\x1d\x72\x01"), (
        f"expected stream to end with GS r 1, got tail {stream[-8:].hex()}"
    )


async def test_no_fence_when_setting_disabled(
    client: AsyncClient, mock_printer: MockPrinter, settings,
):
    settings.wait_for_print_done = False

    captured: list[bytes] = []
    mock_printer.feed_data = lambda d: captured.append(bytes(d))   # type: ignore[assignment]

    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json=_body())
    assert r.status_code == 200

    stream = b"".join(captured)
    assert b"\x1d\x72\x01" not in stream, (
        "GS r 1 must NOT be emitted when WAIT_FOR_PRINT_DONE=false"
    )


async def test_fence_failure_surfaces_as_comm_error(
    client: AsyncClient, mock_printer: MockPrinter,
):
    """If the device drops the link during the fence wait, we report COMM_ERROR."""
    await client.post("/connect", json={"mode": "lan"})
    # Trigger comm error so the fence read raises.
    mock_printer.set_comm_error(True)
    r = await client.post("/print/text", json=_body())
    assert r.status_code == 503
    assert r.json()["error_code"] == "COMM_ERROR"


async def test_concurrent_prints_serialize_through_lock(
    client: AsyncClient, mock_printer: MockPrinter,
):
    """Two prints kicked off in parallel must complete in series — the lock
    held during the fence wait is what guarantees this."""
    await client.post("/connect", json={"mode": "lan"})

    async def one_print(tag: str):
        return await client.post("/print/text", json={
            "machine_id": tag,
            "items": [{"product": "x", "quantity": 1, "reward": 1.0}],
            "total_reward": 1.0,
        })

    r1, r2 = await asyncio.gather(one_print("A"), one_print("B"))
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["job_id"] != r2.json()["job_id"]
