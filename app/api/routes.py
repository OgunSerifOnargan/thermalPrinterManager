"""HTTP routes — thin layer: validate, delegate, map. Grows per phase."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.dependencies import (
    get_eta,
    get_manager,
    get_predictor,
    get_repository,
    get_service,
)
from app.core.auth import require_config_token
from app.core.config import get_settings
from app.core.rate_limit import print_rate_limit
from app.core.errors import CommError, ERROR_POLICY, ErrorCode, PrinterError
from app.devices.mock_preview import preview as build_preview
from app.core.states import DisconnectReason
from app.models.schemas import (
    ActivityInfo,
    ConfigPatchRequest,
    ConfigPatchResponse,
    ConnectionInfo,
    ConnectRequest,
    ConnectResponse,
    DeviceInfo,
    DisconnectResponse,
    EtaInfo,
    HealthResponse,
    LastJobInfo,
    PredictionInfo,
    PreviewResponse,
    PrintImageRequest,
    PrintResponse,
    PrintTextRequest,
    ReconnectInfo,
    ReprintRequest,
    StatusResponse,
)
from app.services import log_reader, usb_inventory
from app.services.connection_manager import ConnectionManager
from app.services.eta import EtaService
from app.services.image_processor import ImageValidationError
from app.services.job_repository import JobRepository
from app.services.prediction import PaperPredictor
from app.services.printer_service import PrinterService


router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/assets/logo")
async def ui_logo():
    """Serve the configured receipt logo so the UI preview can display it.

    NOT under /ui/* — that path is owned by the StaticFiles mount and would
    swallow this route, returning 404.
    """
    from pathlib import Path
    from fastapi.responses import FileResponse
    settings = get_settings()
    if not settings.logo_path:
        raise HTTPException(status_code=404, detail="no logo configured")
    p = Path(settings.logo_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail=f"logo file not found: {p}")
    return FileResponse(p, media_type="image/png")


@router.get("/preview/qr")
async def ui_qr(data: str, box: int = 8):
    """Generate a PNG QR code from the given `data` string for the UI preview.

    NOT a static asset — the real printer renders QR codes itself via the
    `GS ( k` command from the `qr_content` field we send it. This endpoint
    exists so the browser preview pane can show *what* the QR will encode
    before the customer takes the receipt.
    """
    from io import BytesIO
    import qrcode
    from fastapi.responses import StreamingResponse
    img = qrcode.make(data, box_size=max(2, min(box, 16)), border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.get("/usb/devices")
async def usb_devices() -> dict:
    """List USB-CDC serial ports + libusb devices visible to the host.

    UI uses this to populate the device-picker dropdown so users don't
    memorize hex VID/PID strings. `/discover/usb` is the symmetric
    alias to `/discover/lan` introduced for the auto-discover flow;
    both return the same body.
    """
    return usb_inventory.inventory()


@router.get("/discover/usb")
async def discover_usb() -> dict:
    """Alias of `/usb/devices` — same body, exists for symmetry with
    `/discover/lan` so a generic "discover" client can call both
    without knowing the legacy name."""
    return usb_inventory.inventory()


@router.get("/discover/cable")
async def discover_cable_endpoint(port: int = 9100) -> dict:
    """List every Cashino-shaped printer reachable on the host's wired
    interfaces — same scan that powers `POST /connect {"mode":"lan_direct"}`
    but **read-only**: no auto-connect, no state changes.

    The UI calls this when the user selects "LAN — auto-detect direct
    cable" so the candidates can be rendered in a dropdown (the user
    picks, then presses Connect).

    In `DEV_MODE=true` the result also includes the local mock device
    (a synthetic loopback interface is injected) so the developer can
    rehearse the lan_direct flow without a physical Cashino in the
    loop.
    """
    from app.services import lan_discovery
    settings = get_settings()
    return await lan_discovery.discover_cable(port=port,
                                               dev_mode=settings.dev_mode)


@router.get("/discover/lan")
async def discover_lan(port: int = 9100, strict: bool = True) -> dict:
    """Scan the host's local /24 subnet for Cashino-shaped ESC/POS
    listeners on `port` (default 9100).

    For each host whose port is open, the service sends `DLE EOT n=4`
    (real-time paper-sensor query) and checks that the 1-byte reply
    has the Cashino-shaped reserved-bit pattern.

    * `strict=true` (default) returns only confirmed candidates.
    * `strict=false` returns every TCP-open host — useful for
      "what's even on my network?" debugging.

    Wall-clock latency is ~1-2 seconds (254 hosts probed concurrently).
    Returns `{ local_ip, subnet, port, scanned, tcp_open, candidates }`;
    if the host has no usable network, `local_ip` is null and
    `candidates` is empty with a `reason` field.
    """
    from app.services import lan_discovery
    return await lan_discovery.discover(port=port, strict=strict)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe. Always 200 if process is up — independent of printer state."""
    settings = get_settings()
    return HealthResponse(
        ok=True,
        service="aco-thermal-printer-service",
        version="0.1.0",
        ts=datetime.now(UTC),
        dev_mode=settings.dev_mode,
    )


@router.get("/healthz")
async def healthz(request: Request,
                  manager: ConnectionManager = Depends(get_manager),
                  repository: JobRepository = Depends(get_repository)):
    """G6: deep health — verifies the parts a liveness probe can't catch.

    Reports per-check booleans + overall status. Returns 503 if any
    component is degraded so orchestrators (K8s, Docker healthcheck) can
    restart the pod instead of seeing a false-positive 200.
    """
    checks: dict = {}

    # 1) DB reachable + writable
    try:
        with repository._lock:                                   # noqa: SLF001
            repository._conn.execute("SELECT 1").fetchone()      # noqa: SLF001
        checks["database"] = {"ok": True}
    except Exception as exc:                                     # noqa: BLE001
        checks["database"] = {"ok": False, "error": str(exc)}

    # 2) Reconcile background task alive
    loop = getattr(request.app.state, "reconcile_loop", None)
    task = getattr(loop, "_task", None) if loop else None
    if task is None or task.done():
        checks["reconcile_loop"] = {
            "ok": False,
            "error": "task missing or finished" if task else "loop not started",
        }
    else:
        checks["reconcile_loop"] = {"ok": True}

    # 3) Printer link sanity — only enforced when we expect to be connected.
    if manager.target_mode is None:
        checks["printer_link"] = {"ok": True, "note": "no target_mode set"}
    else:
        connected = manager.connected
        last_seen = manager.cached.last_seen_ts
        stale = (
            last_seen is None
            or (datetime.now(UTC) - last_seen).total_seconds() > 30
        )
        ok = connected and not stale
        checks["printer_link"] = {
            "ok": ok,
            "connected": connected,
            "state": manager.state.value,
            "last_seen_ts": last_seen.isoformat() if last_seen else None,
            "stale": stale,
        }

    overall_ok = all(c["ok"] for c in checks.values())
    body = {
        "ok": overall_ok,
        "ts": datetime.now(UTC).isoformat(),
        "service": "aco-thermal-printer-service",
        "version": "0.1.0",
        "checks": checks,
    }
    if not overall_ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content=body)
    return body


@router.post("/connect", response_model=ConnectResponse)
async def connect(
    req: ConnectRequest,
    manager: ConnectionManager = Depends(get_manager),
):
    if req.mode == "lan_direct":
        return await _connect_lan_direct(manager)

    overrides = req.model_dump(
        exclude={"mode"}, exclude_none=True, exclude_unset=True,
    )
    try:
        await manager.request_connect(req.mode, overrides=overrides)
    except CommError as exc:
        log.warning(
            "connect rejected",
            extra={"op": "connect", "mode": req.mode, "error": str(exc)},
        )
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error_code": "COMM_ERROR", "detail": str(exc)},
        ) from exc
    return ConnectResponse(ok=True, mode=req.mode, state=manager.state.value)


def _err_body(code: ErrorCode, detail: str, **extras) -> dict:
    """Compose an error response body using the codified policy. Keeps
    the user_message_tr / user_message_en strings in one place
    (`ERROR_POLICY`) so the lan_direct branch agrees with the rest of
    the API on banner copy."""
    policy = ERROR_POLICY[code]
    body = {
        "ok": False,
        "error_code": code.value,
        "detail": detail,
        "message_tr": policy.user_message_tr,
        "message_en": policy.user_message_en,
        "ts": datetime.now(UTC).isoformat(),
    }
    body.update(extras)
    return body


async def _connect_lan_direct(manager: ConnectionManager):
    """`POST /connect {"mode":"lan_direct"}` — discover-and-attach.

    Scans every active wired interface, then:
      * 0 candidates → 503 NO_DIRECT_DEVICE (with interfaces list).
      * 1 candidate  → auto-connect via the normal LAN path, 200 with
                       the resolved {host, port, via_interface, rtt_ms}.
      * N>1 candidates → 409 MULTIPLE_CANDIDATES (UI picks one).

    The 200 path delegates to `manager.request_connect("lan", …)`, so
    everything downstream (reconcile loop, polling, INIT-on-connect,
    cached status) behaves identically to a manual `mode: "lan"` call.
    """
    from app.services import lan_discovery
    settings = get_settings()

    result = await lan_discovery.discover_cable(dev_mode=settings.dev_mode)
    cands = result["candidates"]

    if len(cands) == 0:
        body = _err_body(
            ErrorCode.NO_DIRECT_DEVICE,
            detail=(f"scanned {result['scanned']} hosts across "
                    f"{len(result['interfaces'])} cable interface(s); "
                    "no Cashino-shaped reply"),
            interfaces=result["interfaces"],
        )
        return JSONResponse(status_code=503, content=body)

    if len(cands) > 1:
        body = _err_body(
            ErrorCode.MULTIPLE_CANDIDATES,
            detail=f"found {len(cands)} candidates; pick one",
            candidates=cands,
            interfaces=result["interfaces"],
        )
        return JSONResponse(status_code=409, content=body)

    cand = cands[0]
    try:
        await manager.request_connect(
            "lan",
            overrides={"lan_host": cand["host"], "lan_port": cand["port"]},
        )
    except CommError as exc:
        log.warning("lan_direct auto-connect failed",
                    extra={"op": "connect", "mode": "lan_direct",
                           "host": cand["host"], "error": str(exc)})
        body = _err_body(
            ErrorCode.COMM_ERROR,
            detail=f"auto-connect to {cand['host']}:{cand['port']}: {exc}",
        )
        return JSONResponse(status_code=503, content=body)

    return JSONResponse(status_code=200, content={
        "ok": True,
        "mode": "lan_direct",
        "resolved": {
            "host": cand["host"],
            "port": cand["port"],
            "via_interface": cand.get("via_interface"),
            "rtt_ms": cand["rtt_ms"],
        },
        "state": manager.state.value,
        "ts": datetime.now(UTC).isoformat(),
    })


@router.post("/disconnect", response_model=DisconnectResponse)
async def disconnect(
    manager: ConnectionManager = Depends(get_manager),
) -> DisconnectResponse:
    await manager.request_disconnect()
    return DisconnectResponse(ok=True, state=manager.state.value)


@router.get("/status", response_model=StatusResponse)
async def status(
    manager: ConnectionManager = Depends(get_manager),
    predictor: PaperPredictor = Depends(get_predictor),
    eta: EtaService = Depends(get_eta),
) -> StatusResponse:
    """Returns the cached snapshot. Does NOT touch the device (per D4)."""
    c = manager.cached
    lj = manager.last_job
    transport = manager.transport
    mode = transport.mode if transport is not None else None
    psnap = predictor.snapshot()
    esnap = eta.snapshot()
    reconnect_info = None
    if manager.failure_count or manager.last_attempt_error:
        reconnect_info = ReconnectInfo(
            failure_count=manager.failure_count,
            breaker_threshold=manager._settings.breaker_threshold,
            breaker_open=manager.disconnect_reason == DisconnectReason.BREAKER_OPEN,
            next_retry_in_ms=manager.next_retry_in_ms,
            last_attempt_ts=manager.last_attempt_ts,
            last_attempt_error=manager.last_attempt_error,
        )
    return StatusResponse(
        ok=True,
        printer_state=manager.state.value,
        connection=ConnectionInfo(
            mode=mode,
            connected=manager.connected,
            state=manager.state.value,
            target_mode=manager.target_mode,
            disconnect_reason=(
                manager.disconnect_reason.value if manager.disconnect_reason else None
            ),
            last_seen_ts=c.last_seen_ts,
        ),
        device=DeviceInfo(
            paper=c.paper,
            cover=c.cover,
            temperature_c=c.temperature_c,
            overheated=c.overheated,
            jammed=c.jammed,
        ),
        last_job=LastJobInfo(
            job_id=lj.job_id,
            op=lj.op,
            status=lj.status,
            error_code=lj.error_code,
            ts=lj.ts,
        ),
        activity=ActivityInfo(
            busy=manager.lock().locked(),
            current_job_id=None,
            pending_count=0,
        ),
        reconnect=reconnect_info,
        prediction=PredictionInfo(
            paper_consumed_mm=psnap.paper_consumed_mm,
            roll_length_mm=psnap.roll_length_mm,
            remaining_mm=psnap.remaining_mm,
            avg_receipt_mm=psnap.avg_receipt_mm,
            estimated_receipts_remaining=psnap.estimated_receipts_remaining,
        ),
        eta=EtaInfo(
            text_ms=esnap.text_ms,
            image_ms=esnap.image_ms,
            samples_text=esnap.samples_text,
            samples_image=esnap.samples_image,
        ),
    )


@router.patch("/config", response_model=ConfigPatchResponse,
              dependencies=[Depends(require_config_token)])
async def patch_config(req: ConfigPatchRequest) -> ConfigPatchResponse:
    """Atomically tune runtime parameters. Token-guarded (CONFIG_PATCH_TOKEN).

    G8: all-or-nothing. We validate every field against the live Settings
    model first by trial-applying on a copy; if any field fails, we reject
    the whole request without mutating the running config. Without this,
    `{"a": valid, "b": invalid}` left "a" updated and "b" raising — the
    Settings instance drifted into an unknown state.
    """
    settings = get_settings()
    patch = req.model_dump(exclude_none=True, exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="empty patch body")

    # Phase 1 — try each field on a snapshot (model_copy) so failures are
    # detected before we touch the live instance.
    from pydantic import ValidationError as PydanticValidationError
    trial = settings.model_copy(deep=False)
    errors: list[dict] = []
    for k, v in patch.items():
        try:
            setattr(trial, k, v)
        except PydanticValidationError as exc:
            errors.append({"field": k, "error": str(exc).split("\n")[-1].strip()})
        except Exception as exc:                                 # noqa: BLE001
            errors.append({"field": k, "error": str(exc)})
    if errors:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error_code": "INVALID_CONFIG",
                    "errors": errors,
                    "detail": "All-or-nothing: no fields were applied."},
        )

    # Phase 2 — commit. Same validators run again on the live instance, but
    # we've already proven they'll succeed.
    applied: dict = {}
    for k, v in patch.items():
        setattr(settings, k, v)
        applied[k] = getattr(settings, k)
    return ConfigPatchResponse(ok=True, updated=applied)


@router.post("/print/text", response_model=PrintResponse,
             dependencies=[Depends(print_rate_limit)])
async def print_text(
    req: PrintTextRequest,
    service: PrinterService = Depends(get_service),
) -> PrintResponse:
    return await service.print_text(req)


@router.post("/reprint", response_model=PrintResponse,
             dependencies=[Depends(print_rate_limit)])
async def reprint(
    req: ReprintRequest,
    service: PrinterService = Depends(get_service),
) -> PrintResponse:
    return await service.reprint(req.job_id, req.idempotency_key)


@router.get("/jobs/failed")
async def list_failed_jobs(
    limit: int = Query(5, ge=1, le=50),
    repository: JobRepository = Depends(get_repository),
) -> dict:
    """List recently failed jobs (status=ERROR) still inside the
    reprint TTL window. Powers the UI's "recent failed" picker so a
    user can pick a specific UUID to retry without copy/pasting from
    the logs view.

    Response is intentionally light — just the fields the picker
    needs (id, op_type, error_code, ts). `machine_id`, `items`, and
    the raw payload bytes are not exposed; they're scrubbed at write
    time anyway (G3) and listing them here would defeat that.
    """
    settings = get_settings()
    rows = repository.list_failed(
        limit=limit,
        max_age_hours=settings.reprint_max_age_hours,
    )
    return {
        "ok": True,
        "limit": limit,
        "count": len(rows),
        "ttl_hours": settings.reprint_max_age_hours,
        "jobs": [
            {
                "job_id": r.job_id,
                "op_type": r.op_type,
                "error_code": r.error_code,
                "error_detail": r.error_detail,
                "ts": r.ts.isoformat(),
            }
            for r in rows
        ],
    }


@router.post("/reprint/last-failed", response_model=PrintResponse,
             dependencies=[Depends(print_rate_limit)])
async def reprint_last_failed(
    repository: JobRepository = Depends(get_repository),
    service: PrinterService = Depends(get_service),
) -> PrintResponse:
    """Reprint the most recently failed job without needing its UUID.

    A separate endpoint (not a sentinel `job_id` value on /reprint) for
    three reasons:

    * `job_id` is a UUID and stays strictly typed — no magic strings.
    * The "last failed" lookup is a fundamentally different resource
      from "this specific job"; URI distinction matches REST intent.
    * It carries its own error model (`NO_FAILED_JOB` 404) instead of
      overloading /reprint's 404 NOT_FOUND, which is reserved for a
      caller-supplied id that doesn't exist.

    Empty body. The window is the same as /reprint's TTL
    (`REPRINT_MAX_AGE_HOURS`) — once the original is too old to
    reprint, this endpoint can't resurrect it either.
    """
    settings = get_settings()
    rec = repository.get_last_failed_within_ttl(
        max_age_hours=settings.reprint_max_age_hours,
    )
    if rec is None:
        raise PrinterError(ErrorCode.NO_FAILED_JOB,
                           "no failed job inside the reprint TTL window")
    return await service.reprint(rec.job_id, None)


@router.post("/print/image", response_model=PrintResponse,
             dependencies=[Depends(print_rate_limit)])
async def print_image(
    req: PrintImageRequest,
    service: PrinterService = Depends(get_service),
) -> PrintResponse:
    try:
        return await service.print_image(req)
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"ok": False, "error_code": "UNKNOWN_COMMAND", "detail": str(exc)},
        ) from exc


@router.get("/logs")
async def get_logs(
    level: str | None = Query(default=None, pattern="^(debug|info|warning|error|critical)$"),
    limit: int = Query(default=100, ge=1, le=10_000),
) -> dict:
    settings = get_settings()
    path = Path(settings.log_dir) / "service.jsonl"
    entries = log_reader.tail(path, limit=limit, min_level=level)
    return {"entries": entries, "count": len(entries),
            "level_filter": level or "all"}


@router.get("/logs/export")
async def export_logs(
    level: str | None = Query(default=None, pattern="^(debug|info|warning|error|critical)$"),
    format: str = Query(default="csv", pattern="^csv$"),
) -> PlainTextResponse:
    settings = get_settings()
    path = Path(settings.log_dir) / "service.jsonl"
    body = log_reader.export_csv(path, min_level=level)
    return PlainTextResponse(
        content=body,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=logs.csv"},
    )


@router.get("/mock/preview", response_model=PreviewResponse)
async def mock_preview(
    job_id: str | None = None,
    repository: JobRepository = Depends(get_repository),
) -> PreviewResponse:
    """Return a text preview of a job's ESC/POS bytes. Dev/test helper."""
    settings = get_settings()
    if not settings.dev_mode:
        raise HTTPException(status_code=404, detail="dev mode disabled")

    record = repository.get(job_id) if job_id else repository.get_latest()
    if record is None:
        raise HTTPException(status_code=404, detail="no job available")

    text = build_preview(record.payload_bytes, codepage=settings.printer_codepage)
    return PreviewResponse(preview=text, bytes_len=len(record.payload_bytes))
