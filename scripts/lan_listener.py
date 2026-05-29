"""Tiny TCP listener simulating an ESC/POS network printer.

Use it to validate the LAN RealTransport without a physical printer:

    python scripts/lan_listener.py            # listens on 127.0.0.1:9100

Then set in .env:
    TRANSPORT_BACKEND=real
    LAN_HOST=127.0.0.1
    LAN_PORT=9100
    DEFAULT_MODE=lan

Print a receipt; you'll see the raw byte stream + ESC/POS sequences here.

For DLE EOT status reads (0x10 0x04 n), the listener replies with a
"clean device" status byte so the service can complete its print flow.
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading


# Reserved bits 1+4 are always 1 in DLE EOT replies; bit 3 (online) set for a healthy device.
_HEALTHY_BYTES = {
    1: 0b00011010,   # printer: online + reserved
    2: 0b00010010,   # offline: no fault bits
    3: 0b00010010,   # error: nothing
    4: 0b00010010,   # paper: present (no near-end, no end)
}


def handle(conn: socket.socket, addr) -> None:
    print(f"[listener] connection from {addr}", flush=True)
    try:
        buf = bytearray()
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
            # Print incoming bytes hex + ASCII preview
            preview = bytes(b if 32 <= b < 127 else 0x2E for b in chunk).decode("ascii", errors="replace")
            print(f"[listener] {len(chunk):4d} bytes  hex={chunk[:48].hex()}{'...' if len(chunk) > 48 else ''}  ascii='{preview[:48]}'", flush=True)

            # Scan for DLE EOT n status queries and reply per byte
            i = 0
            while i < len(buf) - 2:
                if buf[i] == 0x10 and buf[i + 1] == 0x04:
                    n = buf[i + 2]
                    reply = bytes([_HEALTHY_BYTES.get(n, 0b00010010)])
                    conn.sendall(reply)
                    print(f"[listener] DLE EOT n={n} → 0x{reply.hex()}", flush=True)
                    i += 3
                else:
                    i += 1
            buf = bytearray()
    except OSError as e:
        print(f"[listener] error: {e}", flush=True)
    finally:
        conn.close()
        print(f"[listener] connection {addr} closed", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9100)
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((args.host, args.port))
    s.listen(4)
    print(f"[listener] listening on {args.host}:{args.port}  (Ctrl-C to stop)", flush=True)
    try:
        while True:
            conn, addr = s.accept()
            t = threading.Thread(target=handle, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("[listener] bye", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
