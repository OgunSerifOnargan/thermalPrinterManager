"""Paper consumption tracker → remaining-receipts estimate (per K1).

Approach:
  - Each successful print contributes mm of paper consumed.
  - mm estimate is derived from the rendered ESC/POS bytes (line count + image
    raster height + fixed cut margin). Conservative: failed prints count as
    full mm (don't risk underestimating).
  - The remaining estimate updates with each call.
  - Reset when paper-out is cleared (mock /mock/set_paper, or user loaded new
    roll on real device — caller invokes reset_consumption()).

Sensor truth always wins: if device reports PAPER_OUT, that overrides any
optimistic mm-based estimate (handled at the API layer, not here).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


# 203 dpi: 1 dot = 1/203 inch ≈ 0.125 mm.
# Cashino default line spacing is 30 dots = 3.75 mm (datasheet).
_TEXT_LINE_MM: float = 3.75
# A double-height line consumes 2× the paper of a single line.
_DOUBLE_LINE_FACTOR: float = 2.0
# QR at module_size=10 with our typical payload (~30 chars + ECC level M)
# produces a ~25-29 module wide code: 25*10 = 250 dots ≈ 31 mm. We round to 32.
_QR_BLOCK_MM: float = 32.0
# Fixed per-receipt overhead: just the cutter blade clearance now that
# image / QR / size-double are all accounted for explicitly.
_FIXED_OVERHEAD_MM: float = 6.0
# Default fallback receipt size when we have no samples yet.
_DEFAULT_AVG_RECEIPT_MM: float = 80.0


@dataclass
class PredictionSnapshot:
    paper_consumed_mm: float
    roll_length_mm: int
    remaining_mm: float
    avg_receipt_mm: float
    estimated_receipts_remaining: int | None


class PaperPredictor:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._consumed_mm: float = 0.0
        self._receipt_count: int = 0

    def reset(self) -> None:
        """Called when a fresh paper roll is loaded (sensor cleared)."""
        self._consumed_mm = 0.0
        self._receipt_count = 0

    def record_print(self, payload_bytes: bytes) -> float:
        """Estimate mm consumed by this print and add to total. Returns mm added."""
        mm = self._estimate_mm(payload_bytes)
        self._consumed_mm += mm
        self._receipt_count += 1
        return mm

    def snapshot(self) -> PredictionSnapshot:
        roll = self._settings.roll_length_mm
        avg = (self._consumed_mm / self._receipt_count) if self._receipt_count else _DEFAULT_AVG_RECEIPT_MM
        remaining = max(0.0, roll - self._consumed_mm)
        remaining_receipts = int(remaining // avg) if avg > 0 else None
        return PredictionSnapshot(
            paper_consumed_mm=round(self._consumed_mm, 1),
            roll_length_mm=roll,
            remaining_mm=round(remaining, 1),
            avg_receipt_mm=round(avg, 1),
            estimated_receipts_remaining=remaining_receipts,
        )

    # ----- Internal -----

    @staticmethod
    def _estimate_mm(payload: bytes) -> float:
        """Approximate paper length consumed by an ESC/POS byte stream.

        Counts:
          - LF (0x0A) and ESC d n line feeds → _TEXT_LINE_MM per line
            (doubled while the current character size has double-height set)
          - GS v 0 raster image height → 1 dot = 0.125 mm
          - GS ( k QR print commands → _QR_BLOCK_MM each
          - Fixed cutter-clearance overhead
        """
        lines_normal = 0
        lines_double = 0
        image_dots = 0
        qr_blocks = 0
        size_double_h = False                  # ESC ! n with bit 4 set

        i, n = 0, len(payload)
        while i < n:
            b = payload[i]

            if b == 0x0A:                       # LF
                if size_double_h:
                    lines_double += 1
                else:
                    lines_normal += 1
                i += 1
                continue

            if b == 0x1B and i + 2 < n:
                cmd = payload[i + 1]
                if cmd == 0x64:                # ESC d n  feed n lines
                    feeds = payload[i + 2]
                    if size_double_h:
                        lines_double += feeds
                    else:
                        lines_normal += feeds
                    i += 3
                    continue
                if cmd == 0x21:                # ESC ! n  character mode
                    size_double_h = bool(payload[i + 2] & 0x10)
                    i += 3
                    continue

            if b == 0x1D and i + 7 < n and payload[i + 1] == 0x76 and payload[i + 2] == 0x30:
                # GS v 0 m xL xH yL yH d…  raster image
                yL = payload[i + 6]
                yH = payload[i + 7]
                height = yL | (yH << 8)
                image_dots += height
                xL = payload[i + 4]
                xH = payload[i + 5]
                wbytes = xL | (xH << 8)
                i += 8 + wbytes * height
                continue

            if b == 0x1D and i + 6 < n and payload[i + 1] == 0x28 and payload[i + 2] == 0x6B:
                # GS ( k pL pH cn fn …   QR / 2D commands
                pL = payload[i + 3]
                pH = payload[i + 4]
                blk_len = pL | (pH << 8)
                # Only the "print" command (cn=49, fn=81) actually advances paper.
                if i + 6 < n and payload[i + 5] == 0x31 and payload[i + 6] == 0x51:
                    qr_blocks += 1
                i += 5 + blk_len
                continue

            i += 1

        return (
            lines_normal * _TEXT_LINE_MM
            + lines_double * _TEXT_LINE_MM * _DOUBLE_LINE_FACTOR
            + image_dots * 0.125
            + qr_blocks * _QR_BLOCK_MM
            + _FIXED_OVERHEAD_MM
        )
