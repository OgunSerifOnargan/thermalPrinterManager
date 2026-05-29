"""JSON Lines (JSONL) logging — one event per line, append-only.

Schema (per I1):
    ts, level, op, conn, job_id, status, error_code, error_detail, latency_ms, message
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class JsonlFormatter(logging.Formatter):
    """Emit one JSON object per log record."""

    RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName",
    }

    # G3: keys whose **value** is treated as customer/receipt data and must NOT
    # be written to disk in plaintext. We keep the key so operators see which
    # fields were redacted; the value is replaced with "[REDACTED]". Field
    # names match what the renderer / printer_service attach via extra={...}.
    SENSITIVE_KEYS = {
        "machine_id", "qr_content", "items", "title",
        "total_reward", "reward", "image_base64", "payload_bytes",
    }

    @classmethod
    def _scrub(cls, key: str, value: Any) -> Any:
        if key in cls.SENSITIVE_KEYS and value is not None and value != "":
            return "[REDACTED]"
        return value

    def format(self, record: logging.LogRecord) -> str:
        base: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge any extra={...} fields the caller attached
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                base[key] = self._scrub(key, value)
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Configure root logger: JSONL to file + stderr.

    Idempotent — safe to call multiple times (replaces existing handlers).
    """
    settings = get_settings()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "service.jsonl"

    root = logging.getLogger()
    root.setLevel(settings.log_level)
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = JsonlFormatter()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Quiet noisy libs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
