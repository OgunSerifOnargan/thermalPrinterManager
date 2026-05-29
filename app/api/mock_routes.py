"""Mock control endpoints — only mounted when DEV_MODE=true.

Two execution modes (decided per-request from settings):

  1. In-process (default): poke the singleton `MockPrinter` brain that
     lives inside this FastAPI process.
  2. External (when `MOCK_DEVICE_CONTROL_URL` is set): forward the same
     request body to the standalone mock device server. This lets the
     same UI drive a fully separate process listening on TCP 9100.

The two paths share an identical wire contract, so the Dev Tools panel
doesn't need to know which mode is in use.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.devices.mock_printer import MockPrinter


log = logging.getLogger(__name__)
mock_router = APIRouter(prefix="/mock", tags=["mock"])


def _require_dev_mode():
    if not get_settings().dev_mode:
        raise HTTPException(status_code=404, detail="dev mode disabled")


def _get_mock_printer(request: Request) -> MockPrinter:
    brain = getattr(request.app.state, "mock_printer", None)
    if brain is None:
        raise HTTPException(status_code=503, detail="no mock printer registered")
    return brain


async def _proxy_or_none(endpoint: str, payload: dict | None = None,
                         method: str = "POST") -> tuple[Any, int] | None:
    """If MOCK_DEVICE_CONTROL_URL is set AND we're using the real transport,
    forward the call and return (body, status).

    On mock backend the in-process brain IS the device, so we always poke it
    locally. The external mock service is only meaningful when the printer
    transport is real (LAN/USB), because then nothing in this process is
    handling prints.

    Returns None when caller should handle in-process. Raises HTTPException
    on transport errors so the UI gets a clear "mock device offline" signal.
    """
    settings = get_settings()
    url = (settings.mock_device_control_url or "").rstrip("/")
    if not url or settings.transport_backend != "real":
        return None
    target = f"{url}{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            if method == "GET":
                r = await client.get(target)
            else:
                r = await client.post(target, json=payload or {})
            try:
                body = r.json()
            except Exception:                                    # noqa: BLE001
                body = {"raw": r.text}
            return body, r.status_code
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error_code": "MOCK_DEVICE_OFFLINE",
                    "detail": f"cannot reach {url}: {exc}",
                    "control_url": url},
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail={"ok": False, "error_code": "MOCK_DEVICE_ERROR",
                    "detail": str(exc), "control_url": url},
        ) from exc


# ---------- Request models ----------

class SetPaperRequest(BaseModel):
    lines: int = Field(..., ge=0, le=10_000)


class SetCoverRequest(BaseModel):
    open: bool


class SetJammedRequest(BaseModel):
    jammed: bool


class SetTemperatureRequest(BaseModel):
    celsius: float = Field(..., ge=-20.0, le=120.0)


class TriggerCommErrorRequest(BaseModel):
    active: bool


class RunScenarioRequest(BaseModel):
    scenario: Literal["paper_out", "cover_open", "jam", "overheat",
                      "comm_drop", "recover_all"]
    auto_recover_after_ms: int | None = Field(default=None, ge=100, le=120_000)


# ---------- Device status ----------

@mock_router.get("/device_status", dependencies=[Depends(_require_dev_mode)])
async def device_status() -> dict:
    """UI uses this to decide whether the mock device is reachable."""
    settings = get_settings()
    url = (settings.mock_device_control_url or "").rstrip("/")
    if not url or settings.transport_backend != "real":
        # In mock mode (or no URL configured) the printer transport IS the
        # in-process brain — no external service is involved.
        return {"mode": "in_process", "control_url": None, "reachable": True,
                "label": "In-process",
                "transport_backend": settings.transport_backend}
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(f"{url}/health")
            r.raise_for_status()
            health = r.json()
            return {"mode": "external", "control_url": url, "reachable": True,
                    "label": "External · ONLINE",
                    "state": health.get("state", {})}
    except Exception as exc:                                     # noqa: BLE001
        log.warning("mock device unreachable at %s: %s", url, exc)
        return {"mode": "external", "control_url": url, "reachable": False,
                "label": "External · OFFLINE",
                "error": str(exc)}


# ---------- Control endpoints (in-process or proxy) ----------

@mock_router.post("/set_paper", dependencies=[Depends(_require_dev_mode)])
async def set_paper(req: SetPaperRequest, request: Request) -> dict:
    proxied = await _proxy_or_none("/mock/set_paper", req.model_dump())
    if proxied is not None:
        body, status = proxied
        if status >= 400:
            raise HTTPException(status, body)
        return body
    p = _get_mock_printer(request)
    p.set_paper(req.lines)
    log.info("mock set_paper", extra={"op": "mock", "action": "set_paper",
                                       "lines": req.lines})
    return {"ok": True, "paper_lines": p.paper_lines}


@mock_router.post("/set_cover", dependencies=[Depends(_require_dev_mode)])
async def set_cover(req: SetCoverRequest, request: Request) -> dict:
    proxied = await _proxy_or_none("/mock/set_cover", req.model_dump())
    if proxied is not None:
        body, status = proxied
        if status >= 400:
            raise HTTPException(status, body)
        return body
    p = _get_mock_printer(request)
    p.set_cover(req.open)
    log.info("mock set_cover", extra={"op": "mock", "action": "set_cover",
                                       "open": req.open})
    return {"ok": True, "cover_open": p.cover_open}


@mock_router.post("/set_jammed", dependencies=[Depends(_require_dev_mode)])
async def set_jammed(req: SetJammedRequest, request: Request) -> dict:
    proxied = await _proxy_or_none("/mock/set_jammed", req.model_dump())
    if proxied is not None:
        body, status = proxied
        if status >= 400:
            raise HTTPException(status, body)
        return body
    p = _get_mock_printer(request)
    p.set_jammed(req.jammed)
    log.info("mock set_jammed", extra={"op": "mock", "action": "set_jammed",
                                        "jammed": req.jammed})
    return {"ok": True, "jammed": p.jammed}


@mock_router.post("/set_temperature", dependencies=[Depends(_require_dev_mode)])
async def set_temperature(req: SetTemperatureRequest, request: Request) -> dict:
    proxied = await _proxy_or_none("/mock/set_temperature", req.model_dump())
    if proxied is not None:
        body, status = proxied
        if status >= 400:
            raise HTTPException(status, body)
        return body
    p = _get_mock_printer(request)
    p.set_temperature(req.celsius)
    log.info("mock set_temperature", extra={"op": "mock",
                                             "action": "set_temperature",
                                             "celsius": req.celsius})
    return {"ok": True, "temperature_c": p.temperature(),
            "overheated": p.overheated()}


@mock_router.post("/trigger_comm_error", dependencies=[Depends(_require_dev_mode)])
async def trigger_comm_error(req: TriggerCommErrorRequest, request: Request) -> dict:
    proxied = await _proxy_or_none("/mock/trigger_comm_error", req.model_dump())
    if proxied is not None:
        body, status = proxied
        if status >= 400:
            raise HTTPException(status, body)
        return body
    p = _get_mock_printer(request)
    p.set_comm_error(req.active)
    log.info("mock trigger_comm_error", extra={"op": "mock",
                                                "action": "trigger_comm_error",
                                                "active": req.active})
    return {"ok": True, "comm_error_active": p.comm_error_active}


@mock_router.post("/run_scenario", dependencies=[Depends(_require_dev_mode)])
async def run_scenario(req: RunScenarioRequest, request: Request) -> dict:
    proxied = await _proxy_or_none("/mock/run_scenario", req.model_dump())
    if proxied is not None:
        body, status = proxied
        if status >= 400:
            raise HTTPException(status, body)
        return body

    # In-process branch
    p = _get_mock_printer(request)
    if req.scenario == "paper_out":
        p.set_paper(0)
    elif req.scenario == "cover_open":
        p.set_cover(True)
    elif req.scenario == "jam":
        p.set_jammed(True)
    elif req.scenario == "overheat":
        p.set_temperature(70.0)
    elif req.scenario == "comm_drop":
        p.set_comm_error(True)
    elif req.scenario == "recover_all":
        p.set_paper(500)
        p.set_cover(False)
        p.set_jammed(False)
        p.set_temperature(25.0)
        p.set_comm_error(False)
    log.info("mock run_scenario", extra={"op": "mock", "action": "run_scenario",
                                          "scenario": req.scenario})

    if req.auto_recover_after_ms and req.scenario != "recover_all":
        async def _auto_recover():
            await asyncio.sleep(req.auto_recover_after_ms / 1000.0)
            if req.scenario == "paper_out":
                p.set_paper(500)
            elif req.scenario == "cover_open":
                p.set_cover(False)
            elif req.scenario == "jam":
                p.set_jammed(False)
            elif req.scenario == "overheat":
                p.set_temperature(25.0)
            elif req.scenario == "comm_drop":
                p.set_comm_error(False)
            log.info("mock auto_recover", extra={"op": "mock",
                                                  "action": "auto_recover",
                                                  "scenario": req.scenario})

        asyncio.create_task(_auto_recover())

    return {"ok": True, "scenario": req.scenario}
