"""State enums for printer, jobs, and disconnect reasons.

Two state machines run in parallel (per design D1):
    Printer (global)    : DISCONNECTED ⇄ CONNECTING → IDLE ⇄ PRINTING; any → ERROR
    Job (per-print)     : received → printing → done | error
"""
from __future__ import annotations

from enum import Enum


class PrinterState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    IDLE = "idle"
    PRINTING = "printing"
    ERROR = "error"


class JobStatus(str, Enum):
    RECEIVED = "received"
    PRINTING = "printing"
    DONE = "done"
    ERROR = "error"


class DisconnectReason(str, Enum):
    """Six taxonomies (per C5) — guides whether the reconcile loop attempts reconnect."""

    STARTUP = "startup"                  # .env mode set, initial connect attempt
    USER_REQUESTED = "user_requested"    # explicit POST /disconnect — do NOT auto-reconnect
    MODE_CHANGE = "mode_change"          # new POST /connect with different mode; new connect will follow
    COMM_ERROR = "comm_error"            # was connected, link dropped — attempt reconnect
    CONNECT_FAILED = "connect_failed"    # connect attempt itself failed — retry with backoff
    BREAKER_OPEN = "breaker_open"        # threshold reached — stop retrying, wait for manual intervention


# Which disconnect reasons trigger the reconcile loop to attempt reconnect (per C5).
RECONCILE_ON: frozenset[DisconnectReason] = frozenset({
    DisconnectReason.STARTUP,
    DisconnectReason.COMM_ERROR,
    DisconnectReason.CONNECT_FAILED,
})
