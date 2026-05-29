"""ESC/POS command constants and small helpers.

Kept in one place so renderer/image_processor share the same vocabulary.
Cashino KP-300/301H/302 documents standard Epson-compatible commands.
"""
from __future__ import annotations

ESC = b"\x1b"
GS = b"\x1d"
LF = b"\n"

# Init / reset
INIT = ESC + b"@"

# Alignment
ALIGN_LEFT = ESC + b"a\x00"
ALIGN_CENTER = ESC + b"a\x01"
ALIGN_RIGHT = ESC + b"a\x02"

# Emphasis
BOLD_ON = ESC + b"E\x01"
BOLD_OFF = ESC + b"E\x00"

# Character size (ESC ! n)
SIZE_NORMAL = ESC + b"!\x00"
SIZE_DOUBLE = ESC + b"!\x30"        # 2x width + 2x height
SIZE_DOUBLE_W = ESC + b"!\x20"      # 2x width only
SIZE_DOUBLE_H = ESC + b"!\x10"      # 2x height only

# Font selection (ESC M n): 0 = Font A (default, larger), 1 = Font B (smaller)
FONT_A = ESC + b"M\x00"
FONT_B = ESC + b"M\x01"

# Code page select (ESC t n) — n=13 cp857 on most ESC/POS firmwares
SELECT_CP857 = ESC + b"t\x0d"

# Line spacing (ESC 3 n / ESC 2)
DEFAULT_LINE_SPACING = ESC + b"2"

# Cut
CUT_PARTIAL = GS + b"V\x01"
CUT_FULL = GS + b"V\x00"


def feed_lines(n: int) -> bytes:
    """ESC d n — feed n lines."""
    if not 0 <= n <= 255:
        raise ValueError("feed_lines: 0-255")
    return ESC + b"d" + bytes([n])


def select_codepage(table: int) -> bytes:
    """ESC t n — select character code table (0-255)."""
    return ESC + b"t" + bytes([table & 0xFF])


# ---------------- QR Code (ESC/POS GS ( k) ----------------

def qr_set_model(model: int = 50) -> bytes:
    """Model 2 = 50 (0x32). pL=04, pH=00, cn=49 ('1'), fn=65 ('A')."""
    return GS + b"(k\x04\x00\x31\x41" + bytes([model]) + b"\x00"


def qr_set_module_size(size: int = 6) -> bytes:
    """1-16. Larger = bigger QR on paper."""
    size = max(1, min(16, size))
    return GS + b"(k\x03\x00\x31\x43" + bytes([size])


def qr_set_error_correction(level: int = 49) -> bytes:
    """L=48, M=49, Q=50, H=51."""
    return GS + b"(k\x03\x00\x31\x45" + bytes([level])


def qr_store_data(data: bytes) -> bytes:
    """Store QR payload (max 7089 bytes numeric / 2953 byte)."""
    length = len(data) + 3
    pL = length & 0xFF
    pH = (length >> 8) & 0xFF
    return GS + b"(k" + bytes([pL, pH]) + b"\x31\x50\x30" + data


def qr_print() -> bytes:
    return GS + b"(k\x03\x00\x31\x51\x30"


def qr_full(data: bytes, module_size: int = 6, error_level: int = 49) -> bytes:
    """Convenience: emit the full 5-step QR sequence."""
    return (
        qr_set_model()
        + qr_set_module_size(module_size)
        + qr_set_error_correction(error_level)
        + qr_store_data(data)
        + qr_print()
    )


# ---------------- Raster image (GS v 0) ----------------

def raster_image(width_bytes: int, height: int, data: bytes, mode: int = 0) -> bytes:
    """GS v 0 m xL xH yL yH d... — print raster bit-image.

    width_bytes = ceil(width_pixels / 8)
    height = height in pixels
    data = MSB-first packed bits, top-to-bottom
    """
    xL = width_bytes & 0xFF
    xH = (width_bytes >> 8) & 0xFF
    yL = height & 0xFF
    yH = (height >> 8) & 0xFF
    return GS + b"v0" + bytes([mode, xL, xH, yL, yH]) + data
