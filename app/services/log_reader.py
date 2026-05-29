"""Read JSONL log records — tail, filter, export.

Production file is appended by the standard logging handler. We just parse
it line-by-line and apply level + limit filters.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any


log = logging.getLogger(__name__)


_LEVEL_RANK = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "critical": 50,
}


def _meets_level(entry_level: str, min_level: str | None) -> bool:
    if not min_level:
        return True
    entry_rank = _LEVEL_RANK.get(entry_level.lower(), 0)
    min_rank = _LEVEL_RANK.get(min_level.lower(), 0)
    return entry_rank >= min_rank


def tail(log_path: Path, limit: int = 100,
         min_level: str | None = None) -> list[dict[str, Any]]:
    """Return the most recent `limit` entries, optionally filtered by level."""
    if not log_path.exists():
        return []
    keep: deque[dict[str, Any]] = deque(maxlen=limit)
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not _meets_level(entry.get("level", "info"), min_level):
                continue
            keep.append(entry)
    return list(keep)


def export_csv(log_path: Path, min_level: str | None = None) -> str:
    """Return the entire log as CSV text (header + rows)."""
    entries = tail(log_path, limit=1_000_000, min_level=min_level)
    buf = io.StringIO()
    # Collect all keys across entries (union) for header
    fieldnames: list[str] = ["ts", "level", "op", "logger", "message",
                              "job_id", "error_code", "error_detail",
                              "conn", "mode", "duration_ms", "status"]
    seen = set(fieldnames)
    for e in entries:
        for k in e.keys():
            if k not in seen:
                fieldnames.append(k)
                seen.add(k)
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for e in entries:
        writer.writerow(e)
    return buf.getvalue()
