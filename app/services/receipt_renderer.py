"""Block-based ESC/POS receipt renderer (per H2).

A receipt is a list of blocks (header, machine info, title, reward, table,
divider, qr, footer). Each block is a pure function of its data + settings.
This makes it easy to add languages, customize order, or change widths.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.models.schemas import PrintTextRequest
from app.services.encoding import encode_text
from app.services.escpos_cmds import (
    ALIGN_CENTER,
    ALIGN_LEFT,
    BOLD_OFF,
    BOLD_ON,
    CUT_PARTIAL,
    FONT_A,
    FONT_B,
    INIT,
    LF,
    SIZE_DOUBLE,
    SIZE_NORMAL,
    feed_lines,
    qr_full,
    qr_set_module_size,
    qr_set_model,
    qr_set_error_correction,
    qr_store_data,
    qr_print,
    select_codepage,
)


log = logging.getLogger(__name__)


# cp857 is table 13 (0x0D) on most ESC/POS firmwares. Cashino uses Epson-compatible table.
_CODEPAGE_TABLES: dict[str, int] = {
    "cp857": 13,
    "cp1254": 16,
    "cp437": 0,
}


@dataclass
class _Localized:
    """Every user-visible string on the receipt, in one language.

    Add a new language by adding a row to _PHRASES below — no code changes.
    """
    subtitle: str            # under the ACO logo (sample: "reverse vending recycling systems")
    machine_id_label: str    # "MachineID:" prefix
    default_title: str       # used when the request omits `title`
    reward_label: str
    product_header: str
    quantity_header: str
    reward_header: str
    no_items_msg: str
    months: tuple[str, ...]  # 12 month names for the receipt date


_PHRASES: dict[str, _Localized] = {
    "tr": _Localized(
        subtitle="ters yönlü geri dönüşüm sistemleri",
        machine_id_label="Makine No:",
        default_title="Aco Recycling Varsayılan Ödül",
        reward_label="Ödül",
        product_header="Ürün",
        quantity_header="Adet",
        reward_header="Ödül",
        no_items_msg="(ürün yok)",
        months=(
            "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
            "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
        ),
    ),
    "en": _Localized(
        subtitle="reverse vending recycling systems",
        machine_id_label="MachineID:",
        default_title="Aco Recycling Default Reward",
        reward_label="Reward",
        product_header="Product",
        quantity_header="Quantity",
        reward_header="Reward",
        no_items_msg="(no items)",
        months=(
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
    ),
}


class ReceiptRenderer:
    """Stateless: settings injected, render_text/render_image pure."""

    def __init__(self, settings: Settings):
        self._settings = settings
        # Pre-render the header logo once (raster ESC/POS bytes) — saves
        # ~50ms per receipt on PIL resize/dither.
        self._logo_bytes: bytes | None = self._load_logo()

    def _load_logo(self) -> bytes | None:
        path = self._settings.logo_path
        if not path:
            return None
        p = Path(path)
        if not p.is_file():
            log.warning("logo file not found: %s — falling back to text wordmark", p)
            return None
        try:
            from PIL import Image
            from app.services.image_processor import to_escpos_raster
            img = Image.open(p)
            img.load()
            return to_escpos_raster(img, self._settings)
        except Exception as exc:                                 # noqa: BLE001
            log.warning("logo load failed (%s): falling back to text wordmark", exc)
            return None

    # ----- Public API -----

    def render_text(self, req: PrintTextRequest) -> bytes:
        return self._render(req, image_bytes=None)

    def render_image(self, req: PrintTextRequest, image_escpos: bytes) -> bytes:
        return self._render(req, image_bytes=image_escpos)

    # ----- Composition -----

    def _render(self, req: PrintTextRequest, image_bytes: bytes | None) -> bytes:
        s = self._settings
        cols = s.paper_width_cols
        phrases = _PHRASES.get(req.lang, _PHRASES["en"])
        parts: list[bytes] = []

        # Init + code page
        parts.append(INIT)
        cp_table = _CODEPAGE_TABLES.get(s.printer_codepage)
        if cp_table is not None:
            parts.append(select_codepage(cp_table))

        # Everything on the receipt is centered (matches the sample).
        parts.append(ALIGN_CENTER)

        # ----- Header logo -----
        if self._logo_bytes is not None:
            # The logo image already contains the "ACO RECYCLING" wordmark
            # and the subtitle.
            parts += [self._logo_bytes, LF]
        else:
            # Fallback when logo file is missing / unreadable — wordmark is the
            # brand name (kept in English globally), subtitle is localized.
            parts += [
                BOLD_ON, SIZE_DOUBLE,
                self._enc("ACO RECYCLING") + LF,
                SIZE_NORMAL, BOLD_OFF,
                self._enc(phrases.subtitle) + LF, LF,
            ]

        # (User-uploaded image is placed at the bottom — after the QR — so the
        # receipt header always stays clean. See the trailing block below.)

        # ----- Machine info (centered, both lines same size, not bold) -----
        parts += [
            self._enc(f"{phrases.machine_id_label} {req.machine_id}") + LF,
        ]
        if req.timestamp:
            parts.append(self._enc(self._format_date(req.timestamp, phrases)) + LF)

        # ----- Title (Font B = smaller; not bold). Falls back to a localized
        # default so a request without `title` still reads natively. -----
        title_text = req.title or phrases.default_title
        parts += [FONT_B, self._enc(title_text) + LF, FONT_A]

        # ----- Reward (big, bold) -----
        parts += [
            BOLD_ON, SIZE_DOUBLE,
            self._enc(self._reward_line(req.total_reward, phrases)) + LF,
            SIZE_NORMAL, BOLD_OFF,
        ]

        # ----- Items table (boxed; cells centered) -----
        if req.items:
            parts += self._table_block(req.items, phrases, cols)
        else:
            parts.append(self._enc(phrases.no_items_msg) + LF)

        # ----- QR (larger than the previous render to match the sample) -----
        if req.qr_content:
            parts += [
                qr_set_model(),
                qr_set_module_size(10),       # was 6 — bumps the QR to ~50mm
                qr_set_error_correction(49),  # level M
                qr_store_data(req.qr_content.encode("utf-8")),
                qr_print(),
                LF,
            ]

        # ----- User-uploaded image, after the QR -----
        if image_bytes:
            parts += [LF, image_bytes, LF]

        # ----- Cut — small breathing room below the QR.
        # Real Cashino cutter also needs ~10mm clearance; 2 lines + the
        # cut command's own paper advance gives a tidy result.
        parts += [ALIGN_LEFT, feed_lines(2), CUT_PARTIAL]
        return b"".join(parts)

    def _table_block(self, items, phrases: _Localized, cols: int) -> list[bytes]:
        """Sample-style table: full-width horizontal lines, no vertical bars,
        all cells centered. Matches the look of the real receipt photo.
        """
        line = "─" * cols

        # Auto-fit the header words — "Quantity" needs more room than "Adet".
        qty_w = max(7, len(phrases.quantity_header) + 2)
        rwd_w = max(7, len(phrases.reward_header) + 2)
        name_w = cols - qty_w - rwd_w

        def _centered(s, w):
            s = str(s)
            if len(s) > w:
                s = s[: max(0, w - 1)] + "."
            pad = w - len(s)
            left = pad // 2
            right = pad - left
            return " " * left + s + " " * right

        def _row(a, b, c):
            return _centered(a, name_w) + _centered(b, qty_w) + _centered(c, rwd_w)

        out: list[bytes] = []
        out.append(self._enc(line) + LF)
        out.append(self._enc(_row(phrases.product_header,
                                  phrases.quantity_header,
                                  phrases.reward_header)) + LF)
        out.append(self._enc(line) + LF)
        for item in items:
            reward_str = (
                str(int(item.reward))
                if item.reward == int(item.reward)
                else f"{item.reward:.2f}"
            )
            out.append(self._enc(_row(item.product, item.quantity, reward_str)) + LF)
        out.append(self._enc(line) + LF)
        return out

    # ----- Helpers -----

    def _format_date(self, dt, phrases: _Localized) -> str:
        """Convert UTC timestamp to the configured local zone for the receipt.

        Sample-style: '16 Eylül 2025 19:19:02 +03' (TR) or
        '16 September 2025 19:19:02 +03' (EN). Month names come from the
        active language; the zone abbreviation is whatever the OS reports.
        """
        from datetime import UTC
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(self._settings.local_timezone)
        # Treat naive inputs as UTC, then localize.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local = dt.astimezone(tz)
        month = phrases.months[local.month - 1]
        # tzname() handles DST-aware abbreviations (TRT, EST/EDT, etc).
        abbr = local.tzname() or "UTC"
        return (f"{local.day} {month} {local.year} "
                f"{local.hour:02d}:{local.minute:02d}:{local.second:02d} {abbr}")

    @staticmethod
    def _reward_line(amount: float, phrases: _Localized) -> str:
        # Big reward — match sample by using integer when whole.
        if amount == int(amount):
            return f"{phrases.reward_label}: {int(amount)}.00 ₺"
        return f"{phrases.reward_label}: {amount:.2f} ₺"

    # ----- Encoding shim -----

    def _enc(self, text: str) -> bytes:
        return encode_text(text, self._settings)
