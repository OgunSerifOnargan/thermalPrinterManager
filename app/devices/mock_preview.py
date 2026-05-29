"""ESC/POS byte stream → human-readable text preview (mock/dev only).

Best-effort: strips control sequences, decodes printable text via the
configured code page, marks special sequences (QR, image, cut) inline.
"""
from __future__ import annotations


_LIRA_UTF8 = b"\xe2\x82\xba"
_LIRA_SENTINEL = 0x01    # cp857 maps this to control char SOH; safe to swap back to ₺


def preview(data: bytes, codepage: str = "cp857") -> str:
    """Return a textual preview of an ESC/POS byte stream.

    Recognizes the UTF-8 ₺ byte sequence (encoder emits this when
    `use_lira_symbol=True`) and renders it as ₺.
    """
    # Pre-pass: stash UTF-8 ₺ as a single-byte sentinel that cp857 decodes to
    # a harmless control char, which we swap to ₺ after decoding.
    if _LIRA_UTF8 in data:
        data = data.replace(_LIRA_UTF8, bytes([_LIRA_SENTINEL]))

    out: list[str] = []
    i = 0
    n = len(data)
    text_buf: list[int] = []

    def flush_text() -> None:
        if not text_buf:
            return
        try:
            decoded = bytes(text_buf).decode(codepage, errors="replace")
        except LookupError:
            decoded = bytes(text_buf).decode("ascii", errors="replace")
        out.append(decoded.replace(chr(_LIRA_SENTINEL), "₺"))
        text_buf.clear()

    while i < n:
        b = data[i]
        if b == 0x1B:  # ESC
            flush_text()
            if i + 1 < n:
                cmd = data[i + 1]
                if cmd == 0x40:                      # ESC @  init
                    out.append("[INIT]")
                    i += 2
                    continue
                if cmd == 0x61 and i + 2 < n:        # ESC a n
                    align = {0: "LEFT", 1: "CENTER", 2: "RIGHT"}.get(data[i + 2], "?")
                    out.append(f"[ALIGN {align}]")
                    i += 3
                    continue
                if cmd == 0x45 and i + 2 < n:        # ESC E n
                    out.append("[BOLD ON]" if data[i + 2] else "[BOLD OFF]")
                    i += 3
                    continue
                if cmd == 0x21 and i + 2 < n:        # ESC ! n
                    flag = data[i + 2]
                    out.append("[SIZE DOUBLE]" if flag & 0x30 else "[SIZE NORMAL]")
                    i += 3
                    continue
                if cmd == 0x4D and i + 2 < n:        # ESC M n  font selection
                    out.append("[FONT B]" if data[i + 2] else "[FONT A]")
                    i += 3
                    continue
                if cmd == 0x74 and i + 2 < n:        # ESC t n  codepage select
                    out.append(f"[CODEPAGE table={data[i + 2]}]")
                    i += 3
                    continue
                if cmd == 0x64 and i + 2 < n:        # ESC d n  feed n lines
                    out.append("\n" * data[i + 2])
                    i += 3
                    continue
                out.append(f"[ESC ?? 0x{cmd:02X}]")
                i += 2
                continue
            i += 1
            continue
        if b == 0x1D:  # GS
            flush_text()
            if i + 1 < n and data[i + 1] == 0x56:    # GS V  cut
                out.append("\n[CUT]\n")
                i += 3 if i + 2 < n else 2
                continue
            if i + 1 < n and data[i + 1] == 0x76:    # GS v  raster image
                # GS v 0 m xL xH yL yH d...
                if i + 7 < n:
                    xL, xH = data[i + 4], data[i + 5]
                    yL, yH = data[i + 6], data[i + 7]
                    wbytes = xL | (xH << 8)
                    height = yL | (yH << 8)
                    total = wbytes * height
                    out.append(f"[IMAGE {wbytes * 8}x{height}]")
                    i += 8 + total
                    continue
                out.append("[IMAGE ??]")
                i += 2
                continue
            if i + 1 < n and data[i + 1] == 0x28:    # GS (   often QR
                # GS ( k pL pH cn fn ...
                if i + 4 < n:
                    pL, pH = data[i + 3], data[i + 4]
                    blk_len = pL | (pH << 8)
                    if i + 5 < n and data[i + 5] == 0x31:  # cn=49 = QR group
                        if i + 6 < n and data[i + 6] == 0x50:  # fn=80 store
                            payload_end = i + 5 + blk_len
                            payload = data[i + 8:payload_end] if payload_end <= n else b""
                            try:
                                txt = payload.decode("utf-8", errors="replace")
                            except Exception:
                                txt = "<binary>"
                            out.append(f"[QR DATA: {txt}]")
                            i = payload_end if payload_end <= n else n
                            continue
                        if i + 6 < n and data[i + 6] == 0x51:  # fn=81 print
                            out.append("[QR PRINT]")
                            i += 5 + blk_len
                            continue
                        out.append("[QR CMD]")
                        i += 5 + blk_len
                        continue
                    out.append("[GS ( k ...]")
                    i += 5 + blk_len
                    continue
                i += 2
                continue
            out.append(f"[GS ?? 0x{data[i + 1]:02X}]" if i + 1 < n else "[GS]")
            i += 2
            continue
        if b == 0x0A:                                # LF
            text_buf.append(b)
            flush_text()
            i += 1
            continue
        text_buf.append(b)
        i += 1
    flush_text()
    return "".join(out)
