"""Integration: GET /logs + /logs/export."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from httpx import AsyncClient


async def _emit_log_lines(settings) -> None:
    """Ensure the JSONL log file has at least a few entries."""
    log = logging.getLogger("app.test")
    log.info("test info", extra={"op": "test", "job_id": "j1", "status": "done"})
    log.warning("test warn", extra={"op": "test", "job_id": "j2",
                                    "error_code": "PAPER_OUT"})
    log.error("test err", extra={"op": "test", "job_id": "j3"})
    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()


async def test_logs_endpoint_returns_entries(client: AsyncClient, settings):
    # Ensure JSONL handler is wired
    from app.core.logging import setup_logging
    setup_logging()
    await _emit_log_lines(settings)
    r = await client.get("/logs?limit=50")
    assert r.status_code == 200
    body = r.json()
    assert "entries" in body
    assert body["count"] >= 3


async def test_logs_level_filter(client: AsyncClient, settings):
    from app.core.logging import setup_logging
    setup_logging()
    await _emit_log_lines(settings)
    r = await client.get("/logs?level=warning")
    assert r.status_code == 200
    body = r.json()
    for e in body["entries"]:
        assert e["level"].lower() in ("warning", "error", "critical")


async def test_logs_export_csv(client: AsyncClient, settings):
    from app.core.logging import setup_logging
    setup_logging()
    await _emit_log_lines(settings)
    r = await client.get("/logs/export?format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    first_line = r.text.splitlines()[0]
    # Header includes core fields
    for col in ("ts", "level", "op", "message"):
        assert col in first_line


async def test_print_appends_to_logs(client: AsyncClient, settings):
    from app.core.logging import setup_logging
    setup_logging()
    await client.post("/connect", json={"mode": "lan"})
    await client.post("/print/text", json={
        "machine_id": "M", "items": [], "total_reward": 0.0,
    })
    # flush
    for h in logging.getLogger().handlers:
        h.flush()
    r = await client.get("/logs?limit=200")
    body = r.json()
    ops = [e.get("op") for e in body["entries"]]
    # Should contain at least the print or poll ops
    assert any(o == "print_text" or o == "connect" or o == "poll" for o in ops)
