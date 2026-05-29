"""G3: sensitive receipt content gets redacted in JSONL logs."""
from __future__ import annotations

import json
import logging

from app.core.logging import JsonlFormatter


def _emit(extra: dict) -> dict:
    """Run a log record through JsonlFormatter and return the parsed JSON."""
    formatter = JsonlFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    for k, v in extra.items():
        setattr(record, k, v)
    return json.loads(formatter.format(record))


def test_qr_content_redacted():
    out = _emit({"op": "print", "qr_content": "ACO-TEST|3.00|TL"})
    assert out["qr_content"] == "[REDACTED]"
    assert out["op"] == "print"     # non-sensitive key passes through


def test_machine_id_redacted():
    out = _emit({"machine_id": "ACO-TEST-0001-0001"})
    assert out["machine_id"] == "[REDACTED]"


def test_items_and_rewards_redacted():
    out = _emit({"items": [{"product": "Glass", "quantity": 1}],
                 "total_reward": 3.0})
    assert out["items"] == "[REDACTED]"
    assert out["total_reward"] == "[REDACTED]"


def test_safe_keys_preserved():
    out = _emit({
        "op": "print_text",
        "job_id": "abc-123",
        "duration_ms": 42,
        "error_code": "PAPER_OUT",
        "status": "error",
    })
    # Operationally useful fields (no PII) survive.
    assert out["job_id"] == "abc-123"
    assert out["duration_ms"] == 42
    assert out["error_code"] == "PAPER_OUT"
    assert out["status"] == "error"


def test_empty_or_none_not_redacted():
    """Don't write [REDACTED] for empty values — keeps logs less noisy."""
    out = _emit({"qr_content": "", "machine_id": None})
    assert out["qr_content"] == ""
    assert out["machine_id"] is None
