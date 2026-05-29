"""Decode DLE EOT status bytes into structured device status (pure functions)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.errors import ErrorCode


PaperLevel = Literal["ok", "low", "out"]
CoverState = Literal["open", "closed"]


@dataclass(frozen=True)
class DeviceStatus:
    paper: PaperLevel
    cover: CoverState
    overheated: bool
    jammed: bool


def decode_status(n2: int, n3: int, n4: int) -> DeviceStatus:
    """Map DLE EOT register bytes to a structured status snapshot."""
    cover_open = bool(n2 & (1 << 2))
    jammed = bool(n3 & (1 << 2))
    overheated = bool(n3 & (1 << 6))

    paper_end = bool(n4 & (1 << 5)) or bool(n4 & (1 << 6))
    paper_near = bool(n4 & (1 << 2)) or bool(n4 & (1 << 3))

    if paper_end:
        paper: PaperLevel = "out"
    elif paper_near:
        paper = "low"
    else:
        paper = "ok"

    return DeviceStatus(
        paper=paper,
        cover="open" if cover_open else "closed",
        overheated=overheated,
        jammed=jammed,
    )


# Priority order when multiple errors are present simultaneously.
# Higher-priority (more blocking) errors are returned first.
_ERROR_PRIORITY: tuple[ErrorCode, ...] = (
    ErrorCode.COVER_OPEN,
    ErrorCode.PAPER_JAM,
    ErrorCode.OVERHEAT,
    ErrorCode.PAPER_OUT,
)


def first_error(status: DeviceStatus) -> ErrorCode | None:
    """Return the highest-priority hardware error, or None if device is clean."""
    flags = {
        ErrorCode.COVER_OPEN: status.cover == "open",
        ErrorCode.PAPER_JAM: status.jammed,
        ErrorCode.OVERHEAT: status.overheated,
        ErrorCode.PAPER_OUT: status.paper == "out",
    }
    for code in _ERROR_PRIORITY:
        if flags[code]:
            return code
    return None
