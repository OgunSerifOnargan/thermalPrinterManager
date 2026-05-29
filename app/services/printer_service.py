"""PrinterService — orchestrator that drives a print job end-to-end.

Workflow (per D5):
    1. acquire device lock
    2. poll (pre-check) — fail fast on hardware fault
    3. transition state to PRINTING, persist job as PRINTING
    4. send bytes to device
    5. poll (post-check) — catch faults that occurred mid-print
    6. persist outcome, update cached last_job

Idempotency (per E2 + R5):
    If the request carries an `idempotency_key` and a job with that key
    already exists, return that existing record without re-printing.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.errors import CommError, ERROR_POLICY, ErrorCode, PrinterError
from app.core.states import JobStatus
from app.models.schemas import PrintImageRequest, PrintResponse, PrintTextRequest
from app.services.connection_manager import ConnectionManager
from app.services.image_processor import (
    ImageValidationError,
    decode_and_validate,
    to_escpos_raster,
)
from app.services.job_repository import JobRecord, JobRepository
from app.services.prediction import PaperPredictor
from app.services.receipt_renderer import ReceiptRenderer
from app.services.status_decoder import first_error


log = logging.getLogger(__name__)


class ReprintNotFoundError(Exception):
    """Asked to reprint a job_id that does not exist in the store."""


class ReprintExpiredError(Exception):
    """Asked to reprint a job that is older than REPRINT_MAX_AGE_HOURS."""


class ReprintNotEligibleError(Exception):
    """Asked to reprint a job that is not in a reprintable state (e.g. still printing)."""


@dataclass
class PrintOutcome:
    job_id: str
    status: JobStatus
    error_code: str | None
    duration_ms: int


class PrinterService:
    def __init__(
        self,
        manager: ConnectionManager,
        repository: JobRepository,
        renderer: ReceiptRenderer,
        settings: Settings,
        predictor: PaperPredictor | None = None,
        clock: Clock | None = None,
    ):
        self._manager = manager
        self._repo = repository
        self._renderer = renderer
        self._settings = settings
        self._predictor = predictor
        self._clock: Clock = clock or SystemClock()

    # ----- Public API -----

    async def print_text(self, req: PrintTextRequest) -> PrintResponse:
        # Idempotency: existing successful or in-flight job? Return it.
        if req.idempotency_key:
            existing = self._repo.get_by_idempotency_key(req.idempotency_key)
            if existing is not None:
                log.info(
                    "idempotent replay",
                    extra={"op": "print_text", "job_id": existing.job_id,
                           "idempotency_key": req.idempotency_key,
                           "status": existing.status.value},
                )
                return self._to_response(existing, idempotent_replay=True)

        payload = self._renderer.render_text(req)
        self._check_payload_size(payload)
        record = self._new_record(
            op_type="print_text",
            payload=payload,
            idempotency_key=req.idempotency_key,
        )
        record, inserted = self._repo.save(record)
        if not inserted:
            # Race-safe: someone won the idempotency_key — return their job.
            return self._to_response(record, idempotent_replay=True)

        outcome = await self._execute(record.job_id, payload, op_type="print_text")
        return PrintResponse(
            ok=outcome.status == JobStatus.DONE,
            job_id=outcome.job_id,
            status=outcome.status.value,
            error_code=outcome.error_code,
            duration_ms=outcome.duration_ms,
        )

    async def print_image(self, req: PrintImageRequest) -> PrintResponse:
        """Render a receipt that embeds a raster image (per F1 + R6).

        Image is validated (size + MIME), converted to ESC/POS raster bytes,
        then composed into the standard receipt layout.
        """
        if req.idempotency_key:
            existing = self._repo.get_by_idempotency_key(req.idempotency_key)
            if existing is not None:
                return self._to_response(existing, idempotent_replay=True)

        # Validate & convert image (may raise ImageValidationError → 422)
        img = decode_and_validate(req.image_base64, self._settings)
        image_bytes = to_escpos_raster(img, self._settings)

        payload = self._renderer.render_image(req, image_bytes)
        self._check_payload_size(payload)
        record = self._new_record(
            op_type="print_image",
            payload=payload,
            idempotency_key=req.idempotency_key,
        )
        record, inserted = self._repo.save(record)
        if not inserted:
            return self._to_response(record, idempotent_replay=True)

        outcome = await self._execute(record.job_id, payload, op_type="print_image")
        return PrintResponse(
            ok=outcome.status == JobStatus.DONE,
            job_id=outcome.job_id,
            status=outcome.status.value,
            error_code=outcome.error_code,
            duration_ms=outcome.duration_ms,
        )

    async def reprint(self, job_id: str, idempotency_key: str | None = None) -> PrintResponse:
        original = self._repo.get(job_id)
        if original is None:
            raise ReprintNotFoundError(f"job {job_id} not found")

        # TTL check (per R7)
        ttl = timedelta(hours=self._settings.reprint_max_age_hours)
        original_ts = original.ts
        if original_ts.tzinfo is None:
            original_ts = original_ts.replace(tzinfo=UTC)
        if datetime.now(UTC) - original_ts > ttl:
            raise ReprintExpiredError(
                f"job {job_id} is older than {self._settings.reprint_max_age_hours}h"
            )

        # Guard: a successful job is NOT re-printed by mistake. The point
        # of /reprint is recovering a *failed* job; if the caller hands
        # us a DONE uuid (typo, picked the wrong line from logs, etc.),
        # we surface that fact with a 200 + status="already_done" instead
        # of silently producing a duplicate receipt. The caller can still
        # see the original duration_ms so the response is informative.
        # /reprint/last-failed never hits this because it filters on
        # ERROR status by construction.
        if original.status == JobStatus.DONE:
            return PrintResponse(
                ok=True,
                job_id=original.job_id,
                status="already_done",
                error_code=None,
                idempotent_replay=False,
                duration_ms=original.duration_ms,
            )

        # Idempotent: caller may pass a key to dedupe reprint attempts
        if idempotency_key:
            existing = self._repo.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                return self._to_response(existing, idempotent_replay=True)

        # Create a brand-new job carrying the SAME payload bytes (per E4)
        new_record = JobRecord(
            job_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            op_type=f"reprint:{original.op_type}",
            status=JobStatus.RECEIVED,
            error_code=None,
            error_detail=None,
            ts=datetime.now(UTC),
            completed_ts=None,
            duration_ms=None,
            payload_bytes=original.payload_bytes,
        )
        new_record, inserted = self._repo.save(new_record)
        if not inserted:
            return self._to_response(new_record, idempotent_replay=True)

        outcome = await self._execute(new_record.job_id, original.payload_bytes,
                                      op_type=new_record.op_type)
        return PrintResponse(
            ok=outcome.status == JobStatus.DONE,
            job_id=outcome.job_id,
            status=outcome.status.value,
            error_code=outcome.error_code,
            duration_ms=outcome.duration_ms,
        )

    # ----- Internals -----

    async def _execute(self, job_id: str, payload: bytes, op_type: str) -> PrintOutcome:
        started = self._clock.monotonic()

        async with self._manager.lock():
            if not self._manager.connected or self._manager.transport is None:
                self._fail(job_id, ErrorCode.COMM_ERROR, "printer not connected",
                           started)
                raise PrinterError(ErrorCode.COMM_ERROR, "printer not connected")

            # --- 1) Pre-check: poll device, refuse if faulted ---
            pre = await self._manager.poll_locked()
            if pre is None:
                # poll_locked returns None only when it tripped a comm drop;
                # the manager has already moved to DISCONNECTED.
                msg = "pre-check failed: device unresponsive"
                self._fail(job_id, ErrorCode.COMM_ERROR, msg, started)
                raise PrinterError(ErrorCode.COMM_ERROR, msg)
            err = first_error(pre)
            if err is not None:
                msg = f"device fault before print: {err.value}"
                self._fail(job_id, err, msg, started)
                raise PrinterError(err, msg)

            # --- 2) Transition to PRINTING + persist ---
            self._manager.mark_printing()
            self._manager.update_last_job(
                job_id=job_id, op=op_type, status="printing",
                error_code=None, ts=self._clock.now(),
            )
            self._repo.update(job_id, status=JobStatus.PRINTING)

            # --- 3) Send bytes ---
            try:
                await self._manager.transport.send(payload)
            except CommError as exc:
                msg = f"send failed: {exc}"
                self._fail(job_id, ErrorCode.COMM_ERROR, msg, started)
                # The transport handles its own disconnection; nudge manager too
                await self._manager._handle_comm_drop(exc)  # noqa: SLF001
                raise PrinterError(ErrorCode.COMM_ERROR, msg) from exc

            # --- 3.5) Buffer-drain fence — block until the device has
            # physically processed every byte of `payload`. Uses GS r 1
            # whose response is gated on receive-buffer drain (Cashino
            # KP-300 manual p.63). Without this, send() returns the moment
            # bytes hit the kernel queue and we'd release the lock while the
            # device is still printing — a second /print request would pile
            # bytes into the device buffer (which is correct FIFO behavior,
            # but breaks the invariant that "service says DONE → paper exited").
            if self._settings.wait_for_print_done:
                try:
                    await self._manager.transport.await_buffer_drain(
                        timeout_ms=self._settings.wait_for_print_timeout_ms,
                    )
                except CommError as exc:
                    msg = f"fence wait failed: {exc}"
                    self._fail(job_id, ErrorCode.COMM_ERROR, msg, started)
                    await self._manager._handle_comm_drop(exc)  # noqa: SLF001
                    raise PrinterError(ErrorCode.COMM_ERROR, msg) from exc

            # --- 4) Post-check: catch faults occurred during print ---
            try:
                post = await self._manager.poll_locked()
            except CommError as exc:
                msg = f"post-poll failed: {exc}"
                self._fail(job_id, ErrorCode.COMM_ERROR, msg, started)
                raise PrinterError(ErrorCode.COMM_ERROR, msg) from exc

            if post is None:
                msg = "post-check failed: device unresponsive"
                self._fail(job_id, ErrorCode.COMM_ERROR, msg, started)
                raise PrinterError(ErrorCode.COMM_ERROR, msg)
            err = first_error(post)
            if err is not None:
                msg = f"device fault after print: {err.value}"
                self._fail(job_id, err, msg, started)
                raise PrinterError(err, msg)

            # --- 5) Success ---
            duration_ms = int((self._clock.monotonic() - started) * 1000)
            completed = self._clock.now()
            self._repo.update(
                job_id,
                status=JobStatus.DONE,
                completed_ts=completed,
                duration_ms=duration_ms,
            )
            if self._predictor is not None:
                self._predictor.record_print(payload)
            self._manager.mark_print_done()
            self._manager.update_last_job(
                job_id=job_id, op=op_type, status="done",
                error_code=None, ts=completed,
            )
            log.info(
                "print done",
                extra={"op": op_type, "job_id": job_id, "status": "done",
                       "duration_ms": duration_ms},
            )
            return PrintOutcome(job_id=job_id, status=JobStatus.DONE,
                                error_code=None, duration_ms=duration_ms)

    def _fail(self, job_id: str, code: ErrorCode, detail: str, started: float) -> None:
        duration_ms = int((self._clock.monotonic() - started) * 1000)
        completed = self._clock.now()
        self._repo.update(
            job_id,
            status=JobStatus.ERROR,
            error_code=code.value,
            error_detail=detail,
            completed_ts=completed,
            duration_ms=duration_ms,
        )
        policy = ERROR_POLICY[code]
        if policy.enters_error_state:
            self._manager.mark_print_error(code)
        else:
            # UNKNOWN_COMMAND: keep printer in IDLE
            self._manager.mark_print_done()
        self._manager.update_last_job(
            job_id=job_id, status="error", error_code=code.value, ts=completed,
        )
        log.warning(
            "print failed",
            extra={"op": "print", "job_id": job_id, "status": "error",
                   "error_code": code.value, "error_detail": detail,
                   "duration_ms": duration_ms},
        )

    def _new_record(self, op_type: str, payload: bytes,
                    idempotency_key: str | None) -> JobRecord:
        return JobRecord(
            job_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            op_type=op_type,
            status=JobStatus.RECEIVED,
            error_code=None,
            error_detail=None,
            ts=self._clock.now(),
            completed_ts=None,
            duration_ms=None,
            payload_bytes=payload,
        )

    def _check_payload_size(self, payload: bytes) -> None:
        """G7: refuse receipts larger than the printer's receive buffer.

        ESC/POS buffers are typically 8-64KB; sending more makes the tail
        silently disappear. Better to fail fast with a clear error code.
        """
        limit = self._settings.max_receipt_bytes
        if len(payload) > limit:
            raise PrinterError(
                ErrorCode.UNKNOWN_COMMAND,
                f"rendered receipt is {len(payload)} bytes; "
                f"limit is {limit}. Shorten the receipt or raise MAX_RECEIPT_BYTES.",
            )

    @staticmethod
    def _to_response(record: JobRecord, *, idempotent_replay: bool) -> PrintResponse:
        return PrintResponse(
            ok=record.status == JobStatus.DONE,
            job_id=record.job_id,
            status=record.status.value,
            error_code=record.error_code,
            idempotent_replay=idempotent_replay,
            duration_ms=record.duration_ms,
        )
