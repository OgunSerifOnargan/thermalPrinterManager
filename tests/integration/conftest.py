"""Integration test fixtures — app + manager + injected mock transport/printer."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.clock import FakeClock, SystemClock
from app.core.config import Settings, get_settings, reset_settings_for_test
from app.devices.mock_printer import MockPrinter, MockPrinterConfig
from app.devices.mock_transport import MockTransport
from app.devices.transport import ConnectionMode
from app.services.connection_manager import ConnectionManager
from app.services.eta import EtaService
from app.services.job_repository import JobRepository
from app.services.prediction import PaperPredictor
from app.services.printer_service import PrinterService
from app.services.receipt_renderer import ReceiptRenderer
from app.services.reconcile_loop import ReconcileLoop


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    """Isolated settings — log files under tmp, fast polling."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("POLL_INTERVAL_IDLE_MS", "50")
    monkeypatch.setenv("POLL_INTERVAL_PRINTING_MS", "20")
    monkeypatch.setenv("CONNECT_RETRY_BASE_MS", "20")
    monkeypatch.setenv("CONNECT_RETRY_MAX_MS", "200")
    monkeypatch.setenv("BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("LOGO_PATH", "")
    # G5: keep the per-IP rate limit out of the way for tests that fire many
    # prints in a row (overheat recovery, idempotency, etc).
    monkeypatch.setenv("PRINT_RATE_LIMIT_PER_MIN", "0")
    reset_settings_for_test()
    # Clear any rate-limit state left over from prior tests.
    from app.core.rate_limit import reset_rate_limit_buckets
    reset_rate_limit_buckets()
    s = get_settings()
    yield s
    reset_settings_for_test()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def mock_printer() -> MockPrinter:
    """Integration mock_printer uses real time so reconcile-loop tests behave naturally."""
    return MockPrinter(
        config=MockPrinterConfig(
            paper_initial_lines=200,
            paper_low_threshold=20,
            initial_temp=25.0,
            overheat_stop_c=65.0,
            overheat_resume_c=55.0,
        ),
        clock=SystemClock(),
    )


@pytest.fixture
def predictor(settings: Settings) -> PaperPredictor:
    return PaperPredictor(settings=settings)


@pytest.fixture
def manager(settings: Settings, mock_printer: MockPrinter,
            predictor: PaperPredictor) -> ConnectionManager:
    def factory(mode: ConnectionMode, overrides: dict | None = None):
        return MockTransport(printer=mock_printer, mode=mode)

    return ConnectionManager(
        settings=settings,
        transport_factory=factory,
        clock=SystemClock(),
        on_paper_restored=predictor.reset,
    )


@pytest.fixture
async def loop(manager: ConnectionManager, settings: Settings) -> AsyncIterator[ReconcileLoop]:
    rl = ReconcileLoop(manager=manager, settings=settings)
    await rl.start()
    try:
        yield rl
    finally:
        await rl.stop()


@pytest.fixture
def repository(tmp_path) -> JobRepository:
    repo = JobRepository(db_path=str(tmp_path / "jobs.db"))
    yield repo
    repo.close()


@pytest.fixture
def renderer(settings: Settings) -> ReceiptRenderer:
    return ReceiptRenderer(settings=settings)


@pytest.fixture
def eta_service(repository: JobRepository) -> EtaService:
    return EtaService(repository=repository)


@pytest.fixture
def printer_service(manager: ConnectionManager, repository: JobRepository,
                    renderer: ReceiptRenderer, settings: Settings,
                    predictor: PaperPredictor) -> PrinterService:
    return PrinterService(manager=manager, repository=repository,
                          renderer=renderer, settings=settings,
                          predictor=predictor)


@pytest.fixture
async def client(settings: Settings, manager: ConnectionManager,
                 loop: ReconcileLoop, repository: JobRepository,
                 renderer: ReceiptRenderer,
                 printer_service: PrinterService,
                 predictor: PaperPredictor,
                 eta_service: EtaService) -> AsyncIterator[AsyncClient]:
    """ASGI test client with all services wired in (no real lifespan)."""
    from app.main import app
    app.state.manager = manager
    app.state.reconcile_loop = loop
    app.state.settings = settings
    app.state.job_repository = repository
    app.state.printer_service = printer_service
    app.state.predictor = predictor
    app.state.eta_service = eta_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
