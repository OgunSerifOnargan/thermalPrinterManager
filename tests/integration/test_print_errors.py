"""Integration: 6 error scenarios through /print/text + happy path."""
from __future__ import annotations

import asyncio

from httpx import AsyncClient

from app.core.clock import FakeClock
from app.core.states import PrinterState
from app.devices.mock_printer import MockPrinter


def make_print_body(idempotency_key: str | None = None) -> dict:
    return {
        "machine_id": "ACO-TEST-0001-0001",
        "items": [
            {"product": "Glass", "quantity": 0, "reward": 0.0},
            {"product": "Plastic", "quantity": 2, "reward": 2.0},
            {"product": "Metal", "quantity": 1, "reward": 1.0},
            {"product": "Tetrapak", "quantity": 0, "reward": 0.0},
        ],
        "total_reward": 3.0,
        "timestamp": "2026-05-27T22:00:00Z",
        "qr_content": "ACO-TEST-0001-0001|3.00|TL",
        "lang": "tr",
        "title": "Aco Recycling Default Reward",
        **({"idempotency_key": idempotency_key} if idempotency_key else {}),
    }


async def wait_for(predicate, timeout: float = 1.0, interval: float = 0.01):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("timeout")


# ---------------------- Happy path ----------------------

async def test_print_text_happy_path(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "done"
    assert body["error_code"] is None
    assert body["duration_ms"] is not None
    assert body["job_id"]


async def test_print_text_updates_last_job(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json=make_print_body())
    job_id = r.json()["job_id"]
    s = (await client.get("/status")).json()
    assert s["last_job"]["job_id"] == job_id
    assert s["last_job"]["status"] == "done"


async def test_print_text_decrements_paper(client: AsyncClient,
                                            mock_printer: MockPrinter):
    await client.post("/connect", json={"mode": "lan"})
    before = mock_printer.paper_lines
    await client.post("/print/text", json=make_print_body())
    assert mock_printer.paper_lines < before


# ---------------------- 6 error scenarios ----------------------

async def test_paper_out_returns_503(client: AsyncClient, mock_printer: MockPrinter):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_paper(0)
    # wait for poller to mark ERROR
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 503
    body = r.json()
    assert body["error_code"] == "PAPER_OUT"
    assert "kağıt" in body["message_tr"].lower()


async def test_paper_jam_returns_503(client: AsyncClient, mock_printer: MockPrinter):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_jammed(True)
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 503
    assert r.json()["error_code"] == "PAPER_JAM"


async def test_cover_open_returns_503(client: AsyncClient, mock_printer: MockPrinter):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 503
    assert r.json()["error_code"] == "COVER_OPEN"


async def test_overheat_returns_503_then_recovers_with_time(
    client: AsyncClient, mock_printer: MockPrinter
):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_temperature(70.0)
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 503
    assert r.json()["error_code"] == "OVERHEAT"
    # Force cool below resume threshold and re-attempt
    mock_printer.set_temperature(50.0)
    r2 = await client.post("/print/text", json=make_print_body())
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"


async def test_comm_error_returns_503(client: AsyncClient, mock_printer: MockPrinter,
                                      manager):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_comm_error(True)
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 503
    body = r.json()
    assert body["error_code"] == "COMM_ERROR"
    # Manager should have moved to DISCONNECTED with comm_error reason
    await wait_for(lambda: manager.state == PrinterState.DISCONNECTED, timeout=1.0)
    assert manager.disconnect_reason.value == "comm_error"


async def test_print_without_connection_returns_503(client: AsyncClient):
    # Never call /connect
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 503
    assert r.json()["error_code"] == "COMM_ERROR"


async def test_unknown_command_via_invalid_body_returns_422(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    # Bad: missing machine_id; FastAPI/Pydantic should reject before service
    r = await client.post("/print/text", json={"items": []})
    assert r.status_code == 422


async def test_state_transitions_during_print(client: AsyncClient, manager):
    await client.post("/connect", json={"mode": "lan"})
    assert manager.state == PrinterState.IDLE
    r = await client.post("/print/text", json=make_print_body())
    assert r.status_code == 200
    assert manager.state == PrinterState.IDLE   # back to IDLE after done


async def test_unknown_command_does_not_enter_error_state(client: AsyncClient, manager):
    """Bad input shouldn't punish the device — printer stays IDLE."""
    await client.post("/connect", json={"mode": "lan"})
    await client.post("/print/text", json={"items": []})   # 422
    assert manager.state == PrinterState.IDLE
