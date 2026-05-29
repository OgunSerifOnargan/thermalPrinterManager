"""Turkish-safe text encoder for cp857 thermal printers (per H5 + R1).

Strategy:
  1. Replace ₺ per `use_lira_symbol` setting (default: "TL").
  2. Try native cp857 (which natively contains ş, ğ, ı, İ, ö, ü, ç, Ş, Ğ, Ö, Ü, Ç).
  3. For any character cp857 doesn't have, fall back to a transliteration
     table; if even that fails, emit '?' and warn.

Goal: ALWAYS produce printable bytes — never raise from the print path.
"""
from __future__ import annotations

import logging
from typing import Final

from app.core.config import Settings


log = logging.getLogger(__name__)


# Transliteration for characters cp857 cannot represent.
# Turkish letters listed here as a safety net even though cp857 covers them.
TRANSLITERATION: Final[dict[str, str]] = {
    # Currency
    "₺": "TL",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    # Quotes
    "“": '"', "”": '"',
    "‘": "'", "’": "'",
    "«": '"', "»": '"',
    # Dashes / ellipsis
    "–": "-", "—": "-",
    "…": "...",
    # Bullets
    "•": "*",
    # Turkish chars — only used if cp857 itself fails for some reason
    "ş": "s", "Ş": "S",
    "ğ": "g", "Ğ": "G",
    "ı": "i", "İ": "I",
    "ö": "o", "Ö": "O",
    "ü": "u", "Ü": "U",
    "ç": "c", "Ç": "C",
}


LIRA_UTF8_BYTES = b"\xe2\x82\xba"   # ₺ encoded as UTF-8 (3 bytes)


def encode_text(text: str, settings: Settings) -> bytes:
    """Encode `text` to printer bytes using settings.printer_codepage.

    - Replaces ₺ per settings.use_lira_symbol:
        * False (default, safe for any device): ₺ → "TL"
        * True: ₺ → its UTF-8 byte sequence; mock_preview decodes that back
          to ₺. Real Cashino firmware may or may not honor it — flip this on
          only when you have confirmed glyph support on the target device.
    - Uses transliteration fallback for other unsupported characters.
    - Logs a single WARNING per call if any substitution occurred.
    """
    codepage = settings.printer_codepage

    out = bytearray()
    substitutions = 0
    for ch in text:
        if ch == "₺":
            if settings.use_lira_symbol:
                out += LIRA_UTF8_BYTES
            else:
                out += b"TL"
            continue
        try:
            out += ch.encode(codepage)
            continue
        except UnicodeEncodeError:
            pass
        repl = TRANSLITERATION.get(ch)
        if repl is not None:
            try:
                out += repl.encode(codepage)
                substitutions += 1
                continue
            except UnicodeEncodeError:
                pass
        out += b"?"
        substitutions += 1

    if substitutions:
        log.warning(
            "text substitutions during encoding",
            extra={"op": "encode", "codepage": codepage,
                   "substitutions": substitutions},
        )
    return bytes(out)
