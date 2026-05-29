"""Image upload → ESC/POS raster bytes (per F1 + R6).

Validates size, format; resizes to printer width; dithers to 1-bit; emits
GS v 0 raster command.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Final

from PIL import Image, UnidentifiedImageError

from app.core.config import Settings
from app.core.errors import ErrorCode, PrinterError
from app.services.escpos_cmds import LF, raster_image


log = logging.getLogger(__name__)


# Cashino 58mm head = 384 dots; 80mm = 576 dots. Derive from paper_width_cols
# (rough: 8 dots per char in default font → cols * 8, capped at 384/576).
_DOTS_PER_COL: Final[int] = 12

# Allowed image formats (PIL identifies these from header bytes)
ALLOWED_FORMATS: Final[set[str]] = {"PNG", "JPEG", "JPG", "BMP", "GIF"}


class ImageValidationError(Exception):
    """Pre-flight validation failure — surfaced as 422 by route layer."""


def decode_and_validate(b64: str, settings: Settings) -> Image.Image:
    """Decode base64 to a PIL Image, enforcing size + MIME limits.

    Raises:
        ImageValidationError: too big, bad format, undecodable.
    """
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ImageValidationError(f"invalid base64: {exc}") from exc

    if len(raw) > settings.max_image_bytes:
        raise ImageValidationError(
            f"image too large: {len(raw)} bytes > {settings.max_image_bytes}"
        )

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()                          # force decode now to catch corruption
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageValidationError(f"could not decode image: {exc}") from exc

    fmt = (img.format or "").upper()
    if fmt not in ALLOWED_FORMATS:
        raise ImageValidationError(
            f"unsupported format: {fmt}; allowed: {sorted(ALLOWED_FORMATS)}"
        )

    return img


def to_escpos_raster(img: Image.Image, settings: Settings) -> bytes:
    """Resize, dither, and emit GS v 0 raster bytes."""
    target_w = settings.paper_width_cols * _DOTS_PER_COL
    target_w = (target_w // 8) * 8         # ensure multiple of 8

    # Resize preserving aspect; never upscale beyond original
    if img.width > target_w:
        ratio = target_w / img.width
        new_h = max(1, int(img.height * ratio))
        img = img.resize((target_w, new_h), Image.LANCZOS)
    # Convert to 1-bit dithered ("1" mode with FloydSteinberg dither)
    bw = img.convert("L").convert("1", dither=Image.FLOYDSTEINBERG)

    width, height = bw.width, bw.height
    width_bytes = (width + 7) // 8
    pixels = bw.load()

    # Pack pixel rows into MSB-first bytes; black=1 on thermal output
    out = bytearray(width_bytes * height)
    for y in range(height):
        for x in range(width):
            if pixels[x, y] == 0:           # PIL "1": 0=black after invert
                out[y * width_bytes + (x // 8)] |= 1 << (7 - (x % 8))

    return raster_image(width_bytes, height, bytes(out)) + LF
