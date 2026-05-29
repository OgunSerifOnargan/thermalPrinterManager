"""FastAPI application entry point.

Lifespan wires ConnectionManager + ReconcileLoop and tears them down cleanly.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import error_handlers
from app.api.mock_routes import mock_router
from app.api.routes import router as api_router
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.connection_manager import ConnectionManager
from app.services.eta import EtaService
from app.services.factory import get_shared_mock_printer, make_transport
from app.services.job_repository import JobRepository
from app.services.prediction import PaperPredictor
from app.services.printer_service import PrinterService
from app.services.receipt_renderer import ReceiptRenderer
from app.services.reconcile_loop import ReconcileLoop


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    log = logging.getLogger("app.lifespan")

    predictor = PaperPredictor(settings=settings)

    def _on_paper_restored() -> None:
        predictor.reset()
        log.info("paper restored — predictor reset", extra={"op": "paper_restored"})

    manager = ConnectionManager(
        settings=settings,
        transport_factory=lambda mode, overrides=None: make_transport(
            settings, mode, overrides),
        on_paper_restored=_on_paper_restored,
    )
    loop = ReconcileLoop(manager=manager, settings=settings)
    repository = JobRepository(db_path=settings.jobs_db_path)
    # G1: any RECEIVED / PRINTING rows in the DB belong to a previous run that
    # crashed (kill -9, OOM, host reboot). Mark them as ERROR so the UI and
    # reprint flow don't see ghost "printing forever" jobs.
    orphaned = repository.reap_orphan_active_jobs(reason="service restart")
    if orphaned:
        log.warning(
            "reaped orphan active jobs at startup",
            extra={"op": "startup_reap", "count": orphaned},
        )
    # Sliding-window retention: drop jobs older than JOB_RETENTION_DAYS.
    # Runs at startup *and* hourly via the housekeeping task below so
    # long-running services don't accumulate stale BLOBs.
    purged = repository.purge_older_than(settings.job_retention_days)
    if purged:
        log.info(
            "purged old jobs at startup",
            extra={"op": "startup_purge", "count": purged,
                   "retention_days": settings.job_retention_days},
        )
    renderer = ReceiptRenderer(settings=settings)
    eta_service = EtaService(repository=repository)
    printer_service = PrinterService(
        manager=manager,
        repository=repository,
        renderer=renderer,
        settings=settings,
        predictor=predictor,
    )

    app.state.manager = manager
    app.state.reconcile_loop = loop
    app.state.settings = settings
    app.state.job_repository = repository
    app.state.printer_service = printer_service
    app.state.predictor = predictor
    app.state.eta_service = eta_service
    # Mock brain accessible from /mock/* endpoints (only used in mock mode)
    app.state.mock_printer = get_shared_mock_printer(settings)

    await manager.autostart()
    await loop.start()

    # Hourly housekeeping — purges old jobs so jobs.db doesn't grow forever
    # on long-running services that never restart.
    async def _housekeeping():
        while True:
            try:
                await asyncio.sleep(3600)
                n = repository.purge_older_than(settings.job_retention_days)
                if n:
                    log.info(
                        "purged old jobs",
                        extra={"op": "housekeeping_purge", "count": n,
                               "retention_days": settings.job_retention_days},
                    )
            except asyncio.CancelledError:
                raise
            except Exception:                                    # noqa: BLE001
                log.exception("housekeeping tick failed",
                              extra={"op": "housekeeping"})

    housekeeping_task = asyncio.create_task(_housekeeping(), name="housekeeping")

    log.info(
        "service started",
        extra={"op": "startup", "host": settings.host, "port": settings.port,
               "dev_mode": settings.dev_mode, "default_mode": settings.default_mode or None},
    )
    try:
        yield
    finally:
        housekeeping_task.cancel()
        try:
            await housekeeping_task
        except (asyncio.CancelledError, Exception):
            pass
        await loop.stop()
        if manager.connected:
            await manager.request_disconnect()
        repository.close()
        log.info("service stopped", extra={"op": "shutdown"})


app = FastAPI(
    title="Aco Recycling Thermal Printer Service",
    version="0.1.0",
    description="Local microservice driving Cashino KP-300/301H/302 thermal printers.",
    lifespan=lifespan,
)

error_handlers.install(app)
app.include_router(api_router)
app.include_router(mock_router)


_UI_DIR = Path(__file__).parent / "ui"
if _UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/ui/")
