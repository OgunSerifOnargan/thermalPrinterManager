# ESC/POS Protocol Primer (and a Mock-Parser Bug Story)

This document walks through the bits of ESC/POS we actually depend on,
why the print-done detection uses a different command from the periodic
poll, and how a subtle mock-parser bug surfaced in this project as a
false `PAPER_JAM` — plus the fix.

> If you only have five minutes, read [§3 (the two-status-command split)](#3-two-kinds-of-status-real-time-vs-buffered) and [§5 (the case study)](#5-the-paper_jam-case-study).

---

## 1. The wire is a dumb byte pipe

USB-CDC, TCP 9100 and RS-232 are all the same picture: bytes go in one
end and come out the other. The transport has no idea what the bytes mean
— it just carries them.

```
[Service] ─── byte byte byte ... ──> [Printer]
[Service] <── byte byte byte ──────── [Printer]   (status replies)
```

Full duplex. The printer can talk back while the service is mid-write.

Everything above this is the **ESC/POS protocol** layered on top of that
byte stream.

---

## 2. Three kinds of bytes leaving the service

### a) Printable text

Plain bytes encoded in the active code page (we use `cp857` — Turkish
DOS). The printer renders them at the current cursor.

```
48 65 6c 6c 6f   →   "Hello"
```

### b) Control commands

Short fixed-length sequences starting with a control byte:

| Bytes | Meaning |
|---|---|
| `1B 40` | `ESC @` — printer reset (INIT). Clears state. |
| `1B 64 03` | `ESC d 3` — feed 3 lines. |
| `0A` | `LF` — feed one line. |
| `1D 56 01` | `GS V 1` — partial cut. |
| `1B 21 30` | `ESC ! 0x30` — double-size mode on. |

### c) Variable-length data commands

A command opcode followed by a fixed header and then an arbitrary-length
**data section** described by that header. These are what cause subtle
parser bugs (see [§5](#5-the-paper_jam-case-study)).

| Command | Header pattern | Data section length |
|---|---|---|
| **Raster bit-image** | `1D 76 30 m xL xH yL yH` | `(xL+xH·256) · (yL+yH·256)` bytes |
| **QR / NV graphics** (`GS ( k`) | `1D 28 6B pL pH cn fn …` | `pL+pH·256` bytes total (includes cn/fn) |
| **Bit-image** (`ESC *`) | `1B 2A m nL nH` | `(nL+nH·256)` bytes for 8-dot modes (m=0,1), `3·(…)` for 24-dot (m=32,33) |
| **Download bit-image** | `1D 2A x y` | `x · y · 8` bytes |

Inside the data section, any byte value can appear — including bytes
that, in isolation, look like control sequences (e.g. `10 04`, `1D 72`).
**The printer firmware knows it's currently reading data and does not
re-interpret them.** It just counts the bytes and stores them. When the
data is exhausted, it returns to command-scan mode.

This distinction is the entire pivot of [§5](#5-the-paper_jam-case-study).

---

## 3. Two kinds of status: real-time vs buffered

There are two ways to ask a Cashino "what's going on?", and they answer
**different questions**.

### `DLE EOT n` — real-time (`10 04 n`)

Real-time means the firmware processes this command **as soon as it
appears in the byte stream, bypassing the receive buffer**. Even if the
buffer has 5 KB of pending text to print, the printer responds to
`10 04 02` **immediately** with one byte describing the requested status
register.

| `n` | Register | What's reported |
|---|---|---|
| 1 | Printer status | Online / offline / drawer kick |
| 2 | Offline cause | Cover open, paper-feed button, paper end stop |
| 3 | Error cause | Mechanical error (jam), cutter error, head over-temp |
| 4 | Paper sensor | Paper present / near-end / end |

This is what `connection_manager` uses for the **periodic poll** (every
~1 s while idle, ~200 ms while printing). The question it answers is:
**"What is the device's state right now?"**

### `GS r n` — buffered (`1D 72 n`)

`GS r n` is **buffered**. It goes into the receive buffer like text and
cut commands do, and the firmware only emits its 1-byte response when it
has processed **every byte before it** off paper. It's a **barrier /
fence**.

| `n` | Returns |
|---|---|
| 1 / 49 | Paper sensor byte (same as `DLE EOT 4`) |

We use `GS r 1` for the **print-done detection** that wraps every print:
send payload → emit `1D 72 01` → block until the printer answers. The
answer arrives ~`lines × 12 ms` later (250 mm/s, ~3 mm per line) — which
is exactly when the receipt has actually come out.

The question it answers: **"Has everything I sent so far been physically
printed?"**

### Why two commands, not one

You can't replace one with the other:

| | DLE EOT n | GS r 1 |
|---|---|---|
| Polling once a second for the UI? | **Yes** — instant. | No — sequential calls each block for ~200 ms. UI would lag visibly. |
| Detecting "the print really finished"? | No — answers instantly while the print is still feeding. Post-check would look "OK" mid-print. | **Yes** — that's exactly its semantics. |

Our print flow uses both:

```
1) pre-check    →  DLE EOT 2,3,4    (real-time: any reason not to start?)
2) send payload                     (buffered: receipt bytes)
3) GS r 1 fence →  wait for reply   (buffered barrier: when this arrives,
                                     everything in (2) is on paper)
4) post-check  →  DLE EOT 2,3,4    (real-time: did anything fail mid-print?)
```

Reference: Cashino KP-300 User Manual, p.63 (`GS r 1`).

---

## 4. Status-byte decoding

Each `DLE EOT n` returns one byte. Bits 1 and 4 are reserved (always 1
on a Cashino — value `0x12` is the "nothing wrong" baseline). The
interesting bits:

| Register | Bit | Meaning |
|---|---|---|
| n=2 | 2 | Cover open |
| n=2 | 5 | Paper-end stop (out of paper) |
| n=3 | 2 | Mechanical error (jam) |
| n=3 | 3 | Cutter error |
| n=3 | 6 | Head temperature over-limit |
| n=4 | 2,3 | Paper near-end (low) |
| n=4 | 5,6 | Paper-end sensor (out) |

Our decoder in `app/services/status_decoder.py`:

```python
cover_open  = bool(n2 & 0b00000100)   # n2 bit 2
jammed      = bool(n3 & 0b00000100)   # n3 bit 2
overheated  = bool(n3 & 0b01000000)   # n3 bit 6
paper_end   = bool(n4 & 0b00100000) or bool(n4 & 0b01000000)
paper_near  = bool(n4 & 0b00000100) or bool(n4 & 0b00001000)
```

A clean printer returns `n2=0x12`, `n3=0x12`, `n4=0x12`. Paper-low
(below `MOCK_PAPER_LOW_THRESHOLD`) returns `n4=0x1E` (bits 2,3 added).

**Key observation:** the same bit position decodes to **different
things** depending on which register the byte is from. `bit 2 of n3` is
"jam"; `bit 2 of n4` is "paper near-end". If the host's status read
**reads bytes out of order** — e.g. it asks for n=3 but the response
buffer happens to contain a leftover n=4 byte — `0x1E` is decoded as
"jam" and a false `PAPER_JAM` fires. This is the bug in [§5](#5-the-paper_jam-case-study).

---

## 5. The PAPER_JAM case study

> **Symptom.** Paper roll set to 41 lines (= "low" but not out). Mock
> device dashboard shows `jammed: false`. The user clicks Print Text
> in the UI. The service returns `503 PAPER_JAM`. Every single time.
> 20 prints in a row — all `PAPER_JAM`.

### 5.1 Forensic logging

Added raw-byte logging to `connection_manager._poll_unlocked` whenever a
device error is detected. The log line for the bad reads showed:

```
device error  error_code=PAPER_JAM  n2=0x12  n3=0x1e  n4=0x1e
```

`n3` should be `0x12` (clean). It's `0x1e` — **the same byte n4
correctly returns** for paper-near-end. The host's read for n=3 received
n=4's response.

### 5.2 Where the contamination came from

The rendered receipt is ~7 KB of ESC/POS bytes. It contains:

* `INIT` / code page / formatting headers (small).
* The **logo raster** as `GS v 0 m xL xH yL yH …data…` — hundreds to
  thousands of arbitrary bytes.
* The **QR code** as `GS ( k pL pH cn fn …data…` — the QR storage
  payload, also arbitrary bytes.

A scan of one such payload (in `tests/manual/scan_receipt.py`) found:

```
Receipt size: 7316 bytes
Spurious 0x1D 0x72 (GS r) inside payload: 0 hits
Spurious 0x10 0x04 (DLE EOT) inside payload: 2 hits
  at byte 2789: ctx=1100751341b1[10 04]45f44e21a888
  at byte 3845: ctx=10003fa82fd1[10 04]420456848a08
```

Two `10 04` byte pairs occur inside data sections by chance — once
inside the logo raster, once inside the QR payload.

### 5.3 What the naïve mock parser did

Earlier `scripts/mock_device_server.py` scanned the byte stream for
`10 04` and `1D 72` patterns **with no state**:

```python
while i < n:
    if buf[i] == 0x10 and buf[i+1] == 0x04:
        register = buf[i+2]
        status = brain.read_status_byte(register)
        write_back(status)       # ← inline reply, even mid-payload!
        i += 3
        continue
    # …
    i += 1
```

On those two accidental `10 04` bytes inside the data sections, the mock
**fired a status reply**. The replies sat in the TCP receive buffer on
the host side until the service read them.

### 5.4 How that pollutes a later read

The service's print sequence:

```
1) send 7316-byte payload
2) await_buffer_drain  →  drain in_waiting, send GS r 1, read(1)
3) read_status(2,3,4)  for the post-check
```

What actually happened across step 2-3:

* The mock fired two spurious 1-byte responses **while** still processing
  the payload chunks.
* Step 2's drain caught some of them, but the `read(1)` for the **real**
  fence response could pick up a late spurious byte arriving slightly
  after drain finished — completing early.
* The real fence response (`0x1E` paper-near-end), delayed by ~250 ms,
  then arrived **after** the early-return and ended up in `read(1)` for
  `read_status(3)`.
* Decoder reads `n3 = 0x1E` → bit 2 set → `jammed=True` → `PAPER_JAM`.

The bug was 100% reproducible at paper=41 because the data bytes in the
payload were deterministic for the same input.

### 5.5 The fix — stateful, ESC/POS-aware parser

`_EscPosScanner` in `scripts/mock_device_server.py` is a small state
machine carrying `remaining_data_bytes` and `pending_header` **across
chunks**. The loop:

```
while bytes left:
  if remaining_data_bytes > 0:        # we're inside a data section
      consume min(remaining, available)
      continue
  if it's a variable-data command:
      parse header, set remaining_data_bytes, jump past header
      continue
  if it's DLE EOT n:    yield event, consume 3 bytes
  if it's GS r n:       yield event, consume 3 bytes
  otherwise:            ordinary print byte, consume 1
```

Both the TCP handler (`handle_tcp`) and the PTY handler
(`_consume_pty_buffer`) now drive the same scanner. Inside a raster or
QR data section, no command matching happens — exactly as the real
Cashino firmware behaves.

### 5.6 Defense-in-depth on the host

Even with the mock fixed, the service-side transports still **drain
stray bytes from the input buffer** before each DLE EOT read. This
protects against:

* A real printer whose UART layer briefly echoes (rare but
  field-observed).
* Future parser bugs in either side.

Code:

* `app/devices/serial_transport.py` — `self._ser.read(self._ser.in_waiting)` before each DLE EOT n.
* `app/devices/real_transport.py` — non-blocking `recv()` loop until `BlockingIOError` before each `sendall(DLE EOT n)`.

### 5.7 Result

Same payload, same paper=41, same stress test:

| Before | After |
|---|---|
| 20 / 20 `PAPER_JAM` | 20 / 20 `done` |

160 existing tests still pass.

### 5.8 Forensic logging stays

The `raw_n2 / raw_n3 / raw_n4` log fields stay in
`connection_manager.py` so any future status-byte misalignment is one
log line away from diagnosis.

---

## 6. Mental model summary

| Layer | Cares about |
|---|---|
| TCP / USB-CDC | Bytes. Nothing more. |
| ESC/POS | Byte sequences → commands, data sections, text. |
| Buffered commands | Go into the buffer; processed in order; can take seconds. |
| Real-time commands (`DLE` prefix) | Bypass buffer; answered immediately. |
| Variable-data commands | Header + N data bytes; data must **not** be re-parsed. |
| Naïve mock parser | "Anywhere I see `10 04` is DLE EOT." Wrong. |
| Real Cashino / new mock | State machine: command-mode vs data-mode. Correct. |

If you keep this table in mind, the rest of the service code reads
straightforwardly.

---

## References

* Cashino KP-300 User Manual, p.63 — `GS r 1` semantics and timing.
* [ESC/POS Command Reference (Star)](https://www.starmicronics.com/support/Mannualfolder/escpos_cm_en.pdf) — generic real-time vs buffered distinction.
