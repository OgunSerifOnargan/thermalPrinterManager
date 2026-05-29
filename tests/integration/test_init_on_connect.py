"""G4: ESC @ (INIT) must be the first byte the device sees after connect."""
from __future__ import annotations

from httpx import AsyncClient

from app.devices.mock_printer import MockPrinter


async def test_init_byte_sent_immediately_after_connect(
    client: AsyncClient, mock_printer: MockPrinter, manager
):
    """The mock brain consumes feed_data; the first bytes should include ESC @.

    We sniff via the brain's paper-line counter and a feed_data spy: drop a tap
    into the printer instance so we capture every byte the transport hands it.
    """
    captured: list[bytes] = []
    original_feed = mock_printer.feed_data

    def _spy(data: bytes) -> None:
        captured.append(bytes(data))
        original_feed(data)

    mock_printer.feed_data = _spy   # type: ignore[assignment]

    r = await client.post("/connect", json={"mode": "lan"})
    assert r.status_code == 200

    # Concatenate everything fed; the very first non-status bytes should be ESC @.
    all_bytes = b"".join(captured)
    assert all_bytes.startswith(b"\x1b@"), (
        f"expected stream to start with ESC @, got {all_bytes[:8].hex()}"
    )
