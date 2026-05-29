"""ConnectionManager — owns transport, printer state, cached status, write lock.

Single coordination point. Per design A3 + C4 + C5 + D4:
  - target_mode: declarative goal ("be connected via X"). Reconcile loop honors it.
  - disconnect_reason: explains why we are DISCONNECTED, drives auto-reconnect policy.
  - state_change_event: lets background work wake the reconcile loop instead of polling.
  - cached_status: GET /status reads this without touching the device.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from app.core.clock import Clock, SystemClock
from app.core.config import Settings
from app.core.errors import CommError, ErrorCode, PrinterError
from app.core.states import RECONCILE_ON, DisconnectReason, PrinterState
from app.devices.transport import ConnectionMode, DeviceTransport
from app.services.status_decoder import DeviceStatus, decode_status, first_error


log = logging.getLogger(__name__)


TransportFactory = Callable[..., DeviceTransport]   # (mode, overrides?) -> DeviceTransport


@dataclass
class CachedStatus:
    paper: str = "unknown"
    cover: str = "unknown"
    overheated: bool = False
    jammed: bool = False
    temperature_c: float | None = None
    last_error: ErrorCode | None = None
    last_seen_ts: datetime | None = None      # last successful poll


@dataclass
class LastJob:
    job_id: str | None = None
    op: str | None = None
    status: str | None = None
    error_code: str | None = None
    ts: datetime | None = None


class ConnectionManager:
    def __init__(
        self,
        settings: Settings,
        transport_factory: TransportFactory,
        clock: Clock | None = None,
        on_paper_restored: Callable[[], None] | None = None,
    ):
        self._settings = settings
        self._factory = transport_factory
        self._clock: Clock = clock or SystemClock()
        self._on_paper_restored = on_paper_restored

        self._transport: DeviceTransport | None = None
        self._state: PrinterState = PrinterState.DISCONNECTED
        self._target_mode: ConnectionMode | None = None
        self._disconnect_reason: DisconnectReason | None = None

        self._cached = CachedStatus()
        self._last_job = LastJob()

        self._failure_count = 0
        self._next_retry_at: float = 0.0
        self._last_attempt_ts: datetime | None = None
        self._last_attempt_error: str | None = None
        # Per-connect parameter overrides (host/port/vid/pid). Held so the
        # reconcile loop can rebuild the same transport on auto-reconnect.
        self._connect_overrides: dict = {}

        self._lock = asyncio.Lock()           # serialize device I/O
        self._state_event = asyncio.Event()   # wake reconcile loop

    # ----- Read-only accessors -----

    @property
    def state(self) -> PrinterState:
        return self._state

    @property
    def target_mode(self) -> ConnectionMode | None:
        return self._target_mode

    @property
    def disconnect_reason(self) -> DisconnectReason | None:
        return self._disconnect_reason

    @property
    def connected(self) -> bool:
        return self._transport is not None and self._transport.connected

    @property
    def transport(self) -> DeviceTransport | None:
        return self._transport

    @property
    def cached(self) -> CachedStatus:
        return self._cached

    @property
    def last_job(self) -> LastJob:
        return self._last_job

    @property
    def state_event(self) -> asyncio.Event:
        return self._state_event

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def last_attempt_ts(self) -> datetime | None:
        return self._last_attempt_ts

    @property
    def last_attempt_error(self) -> str | None:
        return self._last_attempt_error

    @property
    def next_retry_in_ms(self) -> int | None:
        """Milliseconds until the reconcile loop attempts another reconnect.

        Returns None when no retry is scheduled (connected, user disconnect,
        or breaker open).
        """
        if self._state != PrinterState.DISCONNECTED:
            return None
        if self._disconnect_reason not in RECONCILE_ON:
            return None
        if self._next_retry_at <= 0:
            return 0
        delta = self._next_retry_at - self._clock.monotonic()
        return max(0, int(delta * 1000))

    def update_last_job(self, **fields) -> None:
        for k, v in fields.items():
            if hasattr(self._last_job, k):
                setattr(self._last_job, k, v)
        self._wake()

    # ----- User-initiated lifecycle -----

    async def request_connect(self, mode: ConnectionMode,
                              overrides: dict | None = None) -> None:
        """User-driven: set target mode and try to connect right now.

        `overrides` (lan_host, lan_port, usb_vendor_id, usb_product_id) are
        per-connect parameters from /connect; kept so the reconcile loop can
        re-use them on auto-reconnect.

        Raises CommError on failure; state ends up DISCONNECTED with reason set.
        """
        if self._transport is not None and self._transport.connected:
            await self._tear_down(DisconnectReason.MODE_CHANGE)

        self._target_mode = mode
        self._connect_overrides = overrides or {}
        self._disconnect_reason = DisconnectReason.MODE_CHANGE
        self._failure_count = 0
        self._next_retry_at = 0.0
        self._wake()
        await self._attempt_connect()

    async def request_disconnect(self) -> None:
        """User-driven disconnect — disables auto-reconnect."""
        self._target_mode = None
        self._connect_overrides = {}
        await self._tear_down(DisconnectReason.USER_REQUESTED)
        self._wake()

    async def autostart(self) -> None:
        """Called at app startup. If DEFAULT_MODE is set, attempt initial connect."""
        if not self._settings.default_mode:
            return
        self._target_mode = self._settings.default_mode  # type: ignore[assignment]
        self._disconnect_reason = DisconnectReason.STARTUP
        try:
            await self._attempt_connect()
        except CommError as exc:
            log.warning(
                "startup connect failed; reconcile loop will retry",
                extra={"op": "startup_connect", "error": str(exc),
                       "mode": self._target_mode},
            )

    # ----- Reconcile / poll surface used by the background loop -----

    async def maybe_reconnect(self) -> bool:
        """Called by reconcile loop. Returns True if a reconnect was attempted."""
        if self._target_mode is None:
            return False
        if self._state != PrinterState.DISCONNECTED:
            return False
        if self._disconnect_reason not in RECONCILE_ON:
            return False
        if self._clock.monotonic() < self._next_retry_at:
            return False
        try:
            await self._attempt_connect()
        except CommError:
            self._schedule_next_retry()
        return True

    async def poll_once(self) -> DeviceStatus | None:
        """Read all status registers under lock. Updates cached + state."""
        if self._transport is None or not self._transport.connected:
            return None
        async with self._lock:
            return await self._poll_unlocked()

    async def _poll_unlocked(self) -> DeviceStatus | None:
        """Caller must hold self._lock."""
        if self._transport is None:
            return None
        try:
            n2 = await self._transport.read_status(2)
            n3 = await self._transport.read_status(3)
            n4 = await self._transport.read_status(4)
        except CommError as exc:
            await self._handle_comm_drop(exc)
            return None

        status = decode_status(n2, n3, n4)
        old_paper = self._cached.paper
        self._cached.paper = status.paper
        self._cached.cover = status.cover
        self._cached.overheated = status.overheated
        self._cached.jammed = status.jammed
        self._cached.last_seen_ts = self._clock.now()
        if (old_paper == "out" and status.paper != "out"
                and self._on_paper_restored is not None):
            try:
                self._on_paper_restored()
            except Exception:                          # noqa: BLE001
                log.exception("on_paper_restored hook failed")
        # Temperature exposed only when transport is mock (RealTransport leaves it None)
        from app.devices.mock_transport import MockTransport
        if isinstance(self._transport, MockTransport):
            self._cached.temperature_c = self._transport.printer.temperature()

        err = first_error(status)
        if err is not None:
            if self._state != PrinterState.ERROR:
                # Include the raw DLE EOT bytes so we can forensically
                # tell whether the printer really reported the fault, or
                # the bytes were misaligned (e.g. a leaked fence response
                # being interpreted as n3 → false PAPER_JAM).
                log.warning(
                    "device error detected",
                    extra={"op": "poll", "error_code": err.value,
                           "paper": status.paper, "cover": status.cover,
                           "jammed": status.jammed, "overheated": status.overheated,
                           "raw_n2": f"0x{n2:02x}", "raw_n3": f"0x{n3:02x}",
                           "raw_n4": f"0x{n4:02x}"},
                )
            self._cached.last_error = err
            self._state = PrinterState.ERROR
        else:
            if self._state in (PrinterState.ERROR, PrinterState.CONNECTING):
                log.info(
                    "device cleared, returning to IDLE",
                    extra={"op": "poll", "from_state": self._state.value},
                )
                self._state = PrinterState.IDLE
            elif self._state == PrinterState.DISCONNECTED:
                # Transport says connected — heal the state to IDLE.
                self._state = PrinterState.IDLE
            self._cached.last_error = None
        self._wake()
        return status

    # ----- Print path uses this to serialize writes -----

    def lock(self) -> asyncio.Lock:
        """The device write lock — print path must acquire before send/read."""
        return self._lock

    async def poll_locked(self) -> DeviceStatus | None:
        """Caller MUST already hold self.lock(). Updates cached + state."""
        return await self._poll_unlocked()

    def mark_printing(self) -> None:
        if self._state == PrinterState.IDLE:
            self._state = PrinterState.PRINTING

    def mark_print_done(self) -> None:
        if self._state == PrinterState.PRINTING:
            self._state = PrinterState.IDLE
        self._wake()

    def mark_print_error(self, code: ErrorCode) -> None:
        self._cached.last_error = code
        if self._state == PrinterState.PRINTING:
            self._state = PrinterState.ERROR
        self._wake()

    async def handle_comm_drop(self, exc: CommError) -> None:
        """Public hook for the print path when a write/read raises CommError."""
        async with self._lock:
            await self._handle_comm_drop(exc)

    # ----- Internal helpers -----

    async def _attempt_connect(self) -> None:
        assert self._target_mode is not None, "target_mode must be set first"
        previous = self._state
        self._state = PrinterState.CONNECTING
        self._last_attempt_ts = self._clock.now()
        self._wake()
        try:
            new_transport = self._factory(self._target_mode, self._connect_overrides)
            await new_transport.connect()
        except CommError as exc:
            self._failure_count += 1
            self._last_attempt_error = str(exc)
            log.warning(
                "connect attempt failed",
                extra={"op": "connect", "mode": self._target_mode,
                       "attempt": self._failure_count, "error": str(exc)},
            )
            self._state = PrinterState.DISCONNECTED
            if self._failure_count >= self._settings.breaker_threshold:
                self._disconnect_reason = DisconnectReason.BREAKER_OPEN
                log.error(
                    "circuit breaker opened — manual /connect required",
                    extra={"op": "breaker_open", "mode": self._target_mode,
                           "failures": self._failure_count},
                )
            else:
                self._disconnect_reason = DisconnectReason.CONNECT_FAILED
            self._wake()
            raise
        except Exception:
            self._state = previous
            self._wake()
            raise

        self._transport = new_transport
        self._failure_count = 0
        self._next_retry_at = 0.0
        self._last_attempt_error = None
        self._disconnect_reason = None
        log.info(
            "connected",
            extra={"op": "connect", "mode": self._target_mode},
        )
        # G4: send ESC @ (INIT) first thing after the link comes up. The device
        # may carry stale state from whatever program touched it last — custom
        # code page, double-size mode, alignment, etc. Without this, our first
        # status poll or print can race against that stale state and produce
        # garbled output. INIT is idempotent and ~2 bytes; cost is nil.
        try:
            await new_transport.send(b"\x1b@")
        except Exception as exc:                                 # noqa: BLE001
            log.warning(
                "INIT after connect failed; continuing",
                extra={"op": "init_after_connect", "error": str(exc)},
            )
        # Probe device state immediately so cached_status is fresh.
        async with self._lock:
            await self._poll_unlocked()

    async def _tear_down(self, reason: DisconnectReason) -> None:
        if self._transport is not None:
            try:
                await self._transport.disconnect()
            except Exception:    # noqa: BLE001  — disconnect must be quiet
                pass
            self._transport = None
        self._state = PrinterState.DISCONNECTED
        self._disconnect_reason = reason
        log.info(
            "disconnected",
            extra={"op": "disconnect", "reason": reason.value},
        )

    async def _handle_comm_drop(self, exc: CommError) -> None:
        """Caller holds the lock. Marks the transport as gone and arms reconnect."""
        log.warning(
            "comm error — link dropped",
            extra={"op": "comm_drop", "error": str(exc)},
        )
        if self._transport is not None:
            try:
                await self._transport.disconnect()
            except Exception:
                pass
            self._transport = None
        self._state = PrinterState.DISCONNECTED
        self._disconnect_reason = DisconnectReason.COMM_ERROR
        self._failure_count = 0
        self._next_retry_at = 0.0
        self._wake()

    def _schedule_next_retry(self) -> None:
        factor = self._settings.backoff_factor ** max(0, self._failure_count - 1)
        delay_ms = min(
            self._settings.connect_retry_base_ms * factor,
            self._settings.connect_retry_max_ms,
        )
        self._next_retry_at = self._clock.monotonic() + delay_ms / 1000.0
        log.info(
            "next reconnect scheduled",
            extra={"op": "retry_scheduled", "delay_ms": int(delay_ms),
                   "attempt": self._failure_count},
        )

    def _wake(self) -> None:
        self._state_event.set()
