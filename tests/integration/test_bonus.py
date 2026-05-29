"""Integration: prediction in /status, ETA after prints, PATCH /config, auth."""
from __future__ import annotations

from httpx import AsyncClient


def base_body() -> dict:
    return {
        "machine_id": "M-BONUS",
        "items": [{"product": "Plastic", "quantity": 1, "reward": 1.0}],
        "total_reward": 1.0,
        "timestamp": "2026-05-27T22:00:00Z",
        "lang": "tr",
    }


# ----- Prediction -----

async def test_status_includes_prediction(client: AsyncClient):
    s = (await client.get("/status")).json()
    assert "prediction" in s
    assert s["prediction"]["roll_length_mm"] > 0
    assert s["prediction"]["paper_consumed_mm"] == 0.0


async def test_prediction_decreases_after_print(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    before = (await client.get("/status")).json()["prediction"]
    await client.post("/print/text", json=base_body())
    after = (await client.get("/status")).json()["prediction"]
    assert after["paper_consumed_mm"] > before["paper_consumed_mm"]
    assert after["remaining_mm"] < before["remaining_mm"]


async def test_prediction_resets_on_paper_restored(client: AsyncClient, mock_printer):
    from app.main import app
    app.state.mock_printer = mock_printer
    await client.post("/connect", json={"mode": "lan"})
    await client.post("/print/text", json=base_body())
    before_consumed = (await client.get("/status")).json()["prediction"]["paper_consumed_mm"]
    assert before_consumed > 0
    # Drop paper to 0; poller marks ERROR with PAPER_OUT
    mock_printer.set_paper(0)
    # Restore — poll transition out→ok fires reset
    import asyncio
    await asyncio.sleep(0.15)
    mock_printer.set_paper(500)
    await asyncio.sleep(0.15)
    snap = (await client.get("/status")).json()["prediction"]
    assert snap["paper_consumed_mm"] == 0.0


# ----- ETA -----

async def test_eta_null_before_any_print(client: AsyncClient):
    s = (await client.get("/status")).json()
    assert s["eta"]["text_ms"] is None
    assert s["eta"]["image_ms"] is None
    assert s["eta"]["samples_text"] == 0


async def test_eta_text_populated_after_print(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    await client.post("/print/text", json=base_body())
    s = (await client.get("/status")).json()
    assert s["eta"]["text_ms"] is not None
    assert s["eta"]["text_ms"] >= 0
    assert s["eta"]["samples_text"] >= 1
    # image still null until an image print
    assert s["eta"]["image_ms"] is None


# ----- PATCH /config -----

async def test_patch_config_updates_setting(client: AsyncClient, settings):
    before = settings.poll_interval_idle_ms
    r = await client.patch("/config", json={"poll_interval_idle_ms": 250})
    assert r.status_code == 200
    body = r.json()
    assert body["updated"]["poll_interval_idle_ms"] == 250
    assert settings.poll_interval_idle_ms == 250
    # Restore
    settings.poll_interval_idle_ms = before


async def test_patch_config_rejects_invalid_value(client: AsyncClient):
    r = await client.patch("/config", json={"poll_interval_idle_ms": 10})  # <50
    assert r.status_code == 422


async def test_patch_config_empty_body_returns_400(client: AsyncClient):
    r = await client.patch("/config", json={})
    assert r.status_code == 400


async def test_patch_config_token_enforced(client: AsyncClient, settings, env):
    env.setenv("CONFIG_PATCH_TOKEN", "supersecret")
    from app.core.config import reset_settings_for_test, get_settings
    reset_settings_for_test()
    # The new settings instance is what auth reads; mutate the app-bound settings too
    s = get_settings()
    assert s.config_patch_token == "supersecret"

    r1 = await client.patch("/config", json={"poll_interval_idle_ms": 200})
    assert r1.status_code == 401

    r2 = await client.patch(
        "/config",
        json={"poll_interval_idle_ms": 200},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r2.status_code == 401

    r3 = await client.patch(
        "/config",
        json={"poll_interval_idle_ms": 200},
        headers={"Authorization": "Bearer supersecret"},
    )
    assert r3.status_code == 200
