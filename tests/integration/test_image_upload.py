"""Integration: /print/image — valid PNG, too big, wrong format."""
from __future__ import annotations

import base64
import io

from httpx import AsyncClient
from PIL import Image


def make_png_b64(w: int = 80, h: int = 40) -> str:
    img = Image.new("RGB", (w, h), "white")
    # Draw a simple pattern
    for x in range(w):
        for y in range(h):
            if (x + y) % 2 == 0:
                img.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def base_body(b64: str) -> dict:
    return {
        "machine_id": "ACO-IMG",
        "items": [{"product": "Plastic", "quantity": 1, "reward": 1.0}],
        "total_reward": 1.0,
        "timestamp": "2026-05-27T22:00:00Z",
        "lang": "tr",
        "image_base64": b64,
    }


async def test_print_image_happy_path(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/image", json=base_body(make_png_b64()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "done"


async def test_print_image_too_large_returns_422(client: AsyncClient, settings):
    await client.post("/connect", json={"mode": "lan"})
    # Generate a big PNG: 1500x1500 noise → multi-MB
    big = make_png_b64(1500, 1500)
    # Force-bypass our PIL-size limit by sending raw oversize blob
    oversize = base64.b64encode(b"x" * (settings.max_image_bytes + 100)).decode()
    r = await client.post("/print/image", json=base_body(oversize))
    assert r.status_code == 422
    body = r.json()
    assert body["detail"]["error_code"] == "UNKNOWN_COMMAND"


async def test_print_image_invalid_base64_returns_422(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/image", json=base_body("not!!base64!!"))
    assert r.status_code == 422


async def test_print_image_non_image_bytes_returns_422(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    plain = base64.b64encode(b"this is not an image, just text").decode()
    r = await client.post("/print/image", json=base_body(plain))
    assert r.status_code == 422


async def test_mock_preview_returns_text(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    await client.post("/print/text", json={
        "machine_id": "PREVIEW-TEST",
        "items": [{"product": "Glass", "quantity": 1, "reward": 1.0}],
        "total_reward": 1.0,
        "timestamp": "2026-05-27T22:00:00Z",
        "lang": "en",       # so the label is the English "MachineID:"
    })
    r = await client.get("/mock/preview")
    assert r.status_code == 200
    body = r.json()
    assert "MachineID: PREVIEW-TEST" in body["preview"]
    assert body["bytes_len"] > 0


async def test_mock_preview_404_when_no_jobs(client: AsyncClient):
    r = await client.get("/mock/preview")
    assert r.status_code == 404
