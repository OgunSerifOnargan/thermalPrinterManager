"""Background reconcile loop — drives target_mode toward reality.

One loop, state-driven (per C3, D7):
  - DISCONNECTED + reconcile-on reason → try reconnect (respects backoff)
  - IDLE / ERROR → poll device, update cached status
  - PRINTING → skip (print path owns I/O)
  - CONNECTING → skip (in-flight)

Wake-up is event-driven (state_change_event) so transitions are reflected
immediately, not after the next sleep tick (per R3).
"""
from __future__ import annotations

import asyncio
import logging

from app.core.config import Settings
from app.core.states import PrinterState
from app.services.connection_manager import ConnectionManager


log = logging.getLogger(__name__)


class ReconcileLoop:
    def __init__(self, manager: ConnectionManager, settings: Settings):
        self._manager = manager
        self._settings = settings
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="reconcile-loop")
        log.info("reconcile loop started", extra={"op": "reconcile_start"})

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._manager.state_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        log.info("reconcile loop stopped", extra={"op": "reconcile_stop"})

    async def _run(self) -> None:
        s = self._settings
        while not self._stop.is_set():
            try:
                await self._tick_once()
            except Exception:  # noqa: BLE001  — loop must not die
                log.exception("reconcile tick failed", extra={"op": "reconcile_tick"})

            # Pick sleep interval based on state
            state = self._manager.state
            if state == PrinterState.PRINTING:
                interval_ms = s.poll_interval_printing_ms
            else:
                interval_ms = s.poll_interval_idle_ms

            # Wait either the interval or a state-change wake-up
            self._manager.state_event.clear()
            try:
                await asyncio.wait_for(
                    self._manager.state_event.wait(),
                    timeout=interval_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                pass

    async def _tick_once(self) -> None:
        m = self._manager
        state = m.state

        if state == PrinterState.DISCONNECTED:
            await m.maybe_reconnect()
            return

        if state in (PrinterState.IDLE, PrinterState.ERROR):
            await m.poll_once()
            return

        # CONNECTING / PRINTING → do nothing this tick
