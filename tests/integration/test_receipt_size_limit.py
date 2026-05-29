"""G7: rendered receipt larger than MAX_RECEIPT_BYTES → 400 UNKNOWN_COMMAND."""
from __future__ import annotations

from httpx import AsyncClient


async def test_oversized_receipt_returns_400(client: AsyncClient, settings):
    settings.max_receipt_bytes = 1024     # minimum allowed; forces failure with 20 items
    await client.post("/connect", json={"mode": "lan"})

    body = {
        "machine_id": "OVERSIZED",
        "items": [
            # 40-char product names + QR push receipt well over 1KB
            {"product": f"item-{i:02d}-pad-pad-pad-pad-pad-pad-padxx",
             "quantity": i, "reward": float(i)}
            for i in range(20)
        ],
        "total_reward": 0.0,
        "timestamp": "2026-05-28T10:00:00Z",
        "qr_content": "X" * 200,
        "title": "Y" * 40,
    }
    r = await client.post("/print/text", json=body)
    assert r.status_code == 400, r.text
    payload = r.json()
    assert payload["error_code"] == "UNKNOWN_COMMAND"
    assert "limit is 1024" in payload["detail"]


async def test_normal_receipt_under_limit_passes(client: AsyncClient, settings):
    settings.max_receipt_bytes = 30720
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json={
        "machine_id": "M",
        "items": [{"product": "Glass", "quantity": 1, "reward": 1.0}],
        "total_reward": 1.0,
    })
    assert r.status_code == 200
