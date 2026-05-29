"""ErrorCode enum + ERROR_POLICY dict (per D2b).

Adding a new error type = one row in ERROR_POLICY. No if/elif chain anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    PAPER_OUT = "PAPER_OUT"
    PAPER_JAM = "PAPER_JAM"
    COVER_OPEN = "COVER_OPEN"
    OVERHEAT = "OVERHEAT"
    COMM_ERROR = "COMM_ERROR"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    # `mode: "lan_direct"` outcomes — cable-attached printer discovery.
    NO_DIRECT_DEVICE = "NO_DIRECT_DEVICE"
    MULTIPLE_CANDIDATES = "MULTIPLE_CANDIDATES"
    # `POST /reprint/last-failed` — no eligible failed job inside the TTL window.
    NO_FAILED_JOB = "NO_FAILED_JOB"


class ErrorCategory(str, Enum):
    HARDWARE = "hardware"   # physical device issue, needs user intervention
    COMM = "comm"           # link error, reconnect handles it
    COMMAND = "command"     # bad input, do not enter ERROR state


@dataclass(frozen=True)
class ErrorPolicy:
    code: ErrorCode
    category: ErrorCategory
    # If True, this error pushes Printer state to ERROR (the global state machine).
    enters_error_state: bool
    # If True, the device hardware can clear this itself (e.g. OVERHEAT cooldown).
    # If False, a human must do something (open cover, load paper).
    auto_recoverable: bool
    # Whether the reconcile loop should keep polling while this error is active.
    # ERROR state with auto_recoverable=True needs polling to detect recovery.
    keep_polling: bool
    http_status: int                # per L3 mapping
    user_message_tr: str
    user_message_en: str


ERROR_POLICY: dict[ErrorCode, ErrorPolicy] = {
    ErrorCode.PAPER_OUT: ErrorPolicy(
        code=ErrorCode.PAPER_OUT,
        category=ErrorCategory.HARDWARE,
        enters_error_state=True,
        auto_recoverable=False,
        keep_polling=True,
        http_status=503,
        user_message_tr="Yazıcıda kağıt bitti. Lütfen rulo yerleştirin.",
        user_message_en="Printer is out of paper. Please load a new roll.",
    ),
    ErrorCode.PAPER_JAM: ErrorPolicy(
        code=ErrorCode.PAPER_JAM,
        category=ErrorCategory.HARDWARE,
        enters_error_state=True,
        auto_recoverable=False,
        keep_polling=True,
        http_status=503,
        user_message_tr="Kağıt sıkışması algılandı. Lütfen kağıdı temizleyin.",
        user_message_en="Paper jam detected. Please clear the paper path.",
    ),
    ErrorCode.COVER_OPEN: ErrorPolicy(
        code=ErrorCode.COVER_OPEN,
        category=ErrorCategory.HARDWARE,
        enters_error_state=True,
        auto_recoverable=False,
        keep_polling=True,
        http_status=503,
        user_message_tr="Yazıcı kapağı açık. Lütfen kapatın.",
        user_message_en="Printer cover is open. Please close it.",
    ),
    ErrorCode.OVERHEAT: ErrorPolicy(
        code=ErrorCode.OVERHEAT,
        category=ErrorCategory.HARDWARE,
        enters_error_state=True,
        auto_recoverable=True,           # head cools automatically
        keep_polling=True,
        http_status=503,
        user_message_tr="Yazıcı kafası aşırı ısındı. Soğuma için bekliyor.",
        user_message_en="Print head overheated. Waiting for cooldown.",
    ),
    ErrorCode.COMM_ERROR: ErrorPolicy(
        code=ErrorCode.COMM_ERROR,
        category=ErrorCategory.COMM,
        enters_error_state=True,
        auto_recoverable=True,           # reconnect loop handles it
        keep_polling=False,              # reconcile loop owns the recovery, not polling
        http_status=503,
        user_message_tr="Yazıcı ile iletişim kesildi. Yeniden bağlanılıyor.",
        user_message_en="Lost communication with printer. Attempting to reconnect.",
    ),
    ErrorCode.UNKNOWN_COMMAND: ErrorPolicy(
        code=ErrorCode.UNKNOWN_COMMAND,
        category=ErrorCategory.COMMAND,
        enters_error_state=False,        # bad input, don't punish the device
        auto_recoverable=False,
        keep_polling=False,
        http_status=400,
        user_message_tr="Geçersiz veya tanınmayan komut.",
        user_message_en="Invalid or unrecognized command.",
    ),
    ErrorCode.NO_DIRECT_DEVICE: ErrorPolicy(
        code=ErrorCode.NO_DIRECT_DEVICE,
        category=ErrorCategory.COMMAND,   # bad/unmet input, not a device fault
        enters_error_state=False,
        auto_recoverable=False,
        keep_polling=False,
        http_status=503,                  # link unavailable from caller's view
        user_message_tr="Ethernet kablosuyla bağlı yazıcı bulunamadı. "
                        "Kabloyu kontrol edip tekrar deneyin.",
        user_message_en="No Cashino-shaped printer found on any wired "
                        "interface. Check the cable and retry.",
    ),
    ErrorCode.MULTIPLE_CANDIDATES: ErrorPolicy(
        code=ErrorCode.MULTIPLE_CANDIDATES,
        category=ErrorCategory.COMMAND,
        enters_error_state=False,
        auto_recoverable=False,
        keep_polling=False,
        http_status=409,
        user_message_tr="Birden fazla yazıcı bulundu; lütfen birini seçin.",
        user_message_en="Multiple devices detected; pick one from the list.",
    ),
    ErrorCode.NO_FAILED_JOB: ErrorPolicy(
        code=ErrorCode.NO_FAILED_JOB,
        category=ErrorCategory.COMMAND,
        enters_error_state=False,
        auto_recoverable=False,
        keep_polling=False,
        http_status=404,
        user_message_tr="Yeniden bastırılacak başarısız bir iş yok.",
        user_message_en="No recent failed job available to reprint.",
    ),
}


class PrinterError(Exception):
    """Raised when the device reports a fault. Carries the error code."""

    def __init__(self, code: ErrorCode, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}" if detail else code.value)


class CommError(PrinterError):
    def __init__(self, detail: str = ""):
        super().__init__(ErrorCode.COMM_ERROR, detail)
