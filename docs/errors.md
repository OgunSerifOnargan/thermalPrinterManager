# Error Catalog

Every error in the system carries a stable `ErrorCode` enum value, an
`ErrorPolicy` row (category, recovery semantics, HTTP status, localized
user message), and a documented trigger path. New error types are added
as **one row** in `app/core/errors.py:ERROR_POLICY` — there is no `if /
elif` chain anywhere that has to be updated.

## Error table

| Code | Category | HTTP | Auto-recoverable | `user_message_tr` | `user_message_en` |
|---|---|---|---|---|---|
| `PAPER_OUT` | hardware | 503 | no — user must load paper | Yazıcıda kağıt bitti. Lütfen rulo yerleştirin. | Printer is out of paper. Please load a new roll. |
| `PAPER_JAM` | hardware | 503 | no — user must clear the path | Kağıt sıkışması algılandı. Lütfen kağıdı temizleyin. | Paper jam detected. Please clear the paper path. |
| `COVER_OPEN` | hardware | 503 | no — user must close the cover | Yazıcı kapağı açık. Lütfen kapatın. | Printer cover is open. Please close it. |
| `OVERHEAT` | hardware | 503 | **yes** — head cools | Yazıcı kafası aşırı ısındı. Soğuma için bekliyor. | Print head overheated. Waiting for cooldown. |
| `COMM_ERROR` | comm | 503 | **yes** — reconnect | Yazıcı ile iletişim kesildi. Yeniden bağlanılıyor. | Lost communication with printer. Attempting to reconnect. |
| `UNKNOWN_COMMAND` | command | 400 | n/a — bad input | Geçersiz veya tanınmayan komut. | Invalid or unrecognized command. |
| `NOT_FOUND` | command | 404 | n/a | İş bulunamadı. | Job not found. |
| `GONE` | command | 410 | n/a | İş için yeniden yazdırma süresi geçti. | Reprint window expired for this job. |
| `RATE_LIMITED` | command | 429 | n/a — wait | Çok fazla istek. Lütfen biraz bekleyin. | Too many print requests; please slow down. |
| `NO_DIRECT_DEVICE` | command | 503 | no — check cable | Ethernet kablosuyla bağlı yazıcı bulunamadı. Kabloyu kontrol edip tekrar deneyin. | No Cashino-shaped printer found on any wired interface. Check the cable and retry. |
| `MULTIPLE_CANDIDATES` | command | 409 | n/a — pick one | Birden fazla yazıcı bulundu; lütfen birini seçin. | Multiple devices detected; pick one from the list. |
| `NO_FAILED_JOB` | command | 404 | n/a — nothing to do | Yeniden bastırılacak başarısız bir iş yok. | No recent failed job available to reprint. |

Source: `app/core/errors.py`.

## Response envelope

Every error response has the same shape:

```json
{
  "ok": false,
  "error_code": "PAPER_JAM",
  "detail": "device fault before print: PAPER_JAM",
  "message_tr": "Kağıt sıkışması algılandı. Lütfen kağıdı temizleyin.",
  "message_en": "Paper jam detected. Please clear the paper path.",
  "ts": "2026-05-28T20:00:00Z"
}
```

`detail` is operator-facing (English, may include internal context like
"pre-check failed" or "post-check fence timeout"). `message_tr` /
`message_en` are user-facing and what the UI banner shows.

---

## Per-error cards

### PAPER_OUT

* **Trigger.** `read_status(4)` returns a byte with bit 5 or bit 6 set
  (paper-end sensor). Decoder sets `paper="out"`. `first_error()`
  returns `PAPER_OUT`.
* **HTTP.** `503`.
* **UI banner.** Red, **sticky** (stays until next user action).
* **Recovery.** User loads a new roll. The reconcile loop's next poll
  sees the cleared sensor → state machine transitions `ERROR → IDLE`.
  Service emits `on_paper_restored` hook (used by `PaperPredictor` to
  reset the consumed-mm counter).
* **Reprint?** Yes — the failed job is persisted with `status=ERROR`,
  reprintable for `REPRINT_MAX_AGE_HOURS` (default 24).
* **Simulate.** `POST /mock/run_scenario {"scenario":"paper_out"}` or
  `POST /mock/set_paper {"lines":0}`.
* **Tests.** `tests/unit/test_error_policy.py`,
  `tests/unit/test_mock_printer.py` (paper sensor causality).

### PAPER_JAM

* **Trigger.** `read_status(3)` returns a byte with bit 2 set
  (mechanical error).
* **HTTP.** `503`.
* **UI banner.** Red, sticky.
* **Recovery.** User clears the paper path (open cover → clear jam →
  close cover). On real Cashino this typically clears n=3 bit 2 once the
  obstruction is removed.
* **Reprint?** Yes.
* **Simulate.** `POST /mock/set_jammed {"jammed":true}` or
  `POST /mock/run_scenario {"scenario":"jam","auto_recover_after_ms":3000}`.
* **Look out for.** A false `PAPER_JAM` is the most subtle status-byte
  bug — a misaligned read picks up `n4`'s response (`0x1E` at paper-low)
  as `n3`. See [`escpos.md §5`](escpos.md#5-the-paper_jam-case-study) for
  the full case study and the ESC/POS-aware mock parser fix.
* **Forensic logging.** When `connection_manager` detects any device
  error, it logs `raw_n2 / raw_n3 / raw_n4` so misalignment is
  diagnosable from one log line.

### COVER_OPEN

* **Trigger.** `read_status(2)` bit 2 set.
* **HTTP.** `503`.
* **UI banner.** Red, sticky.
* **Recovery.** User closes the cover. Polled — recovers next tick.
* **Reprint?** Yes.
* **Simulate.** `POST /mock/set_cover {"open":true}` or
  `POST /mock/run_scenario {"scenario":"cover_open","auto_recover_after_ms":3000}`.

### OVERHEAT

* **Trigger.** Head temperature crosses `OVERHEAT_STOP_C` (default 65 °C
  for KP-302) — `read_status(3)` bit 6 set, or the mock brain's modeled
  temperature passes the threshold.
* **HTTP.** `503`.
* **UI banner.** Yellow/red — **auto-clears** when the head cools below
  `OVERHEAT_RESUME_C` (default 55 °C); polling sees the cleared bit and
  state machine returns to IDLE without user action.
* **Reprint?** Yes — try again after the cooldown.
* **Simulate.** `POST /mock/set_temperature {"celsius":72}` or
  `POST /mock/run_scenario {"scenario":"overheat","auto_recover_after_ms":5000}`.
* **Cooldown model.** Mock brain cools at `MOCK_COOL_RATE` °C/s
  (default 0.5). Real Cashino datasheet specifies hysteresis; we trust
  it.

### COMM_ERROR

* **Trigger.** Transport read/write raised, or a status read timed out.
  This is the only error category where **the reconcile loop owns
  recovery**, not the polling loop — `keep_polling=False`,
  `auto_recoverable=True`.
* **HTTP.** `503`.
* **UI banner.** Red. Auto-clears when reconnect succeeds.
* **Recovery flow.**
  1. `connection_manager._handle_comm_drop` marks the link disconnected
     with a `disconnect_reason` (e.g. `read_timeout`, `usb_unplugged`,
     `peer_closed`).
  2. Reconcile loop wakes (via `state_change_event`) and starts
     exponential backoff (`CONNECT_RETRY_BASE_MS` …
     `CONNECT_RETRY_MAX_MS`, factor `BACKOFF_FACTOR`).
  3. After `BREAKER_THRESHOLD` consecutive failures the breaker opens —
     loop stops retrying, UI shows "breaker open", manual `/connect`
     re-arms.
  4. On successful reconnect, INIT (`ESC @`) is sent immediately so
     code page / alignment / size are at known defaults before the next
     print.
* **Reprint?** The failed job is persisted with `status=ERROR`;
  reprint after reconnect works normally.
* **Simulate.** `POST /mock/set_comm_error {"active":true}` (TCP/PTY
  side closes), or pull the USB cable on a real device.

### UNKNOWN_COMMAND

* **Trigger.** Two paths:
  1. Pydantic body validation failed (a required field missing, a string
     too long) → FastAPI 422 with Pydantic's detail.
  2. Rendered payload exceeds `MAX_RECEIPT_BYTES` (default 30720) →
     `PrinterError(UNKNOWN_COMMAND, "receipt too large: N bytes")` →
     400.
* **HTTP.** `400` (size limit) or `422` (Pydantic).
* **UI banner.** Red, sticky.
* **Recovery.** Fix the input. Not the device's fault, so the printer
  state stays `IDLE` (`enters_error_state=False`).
* **Reprint?** No — there's nothing reprintable; the job never persisted.
* **Simulate.** Send 25+ items, or a 5 MB image (image bytes hard cap
  hits `MAX_IMAGE_BYTES=2097152` first → also returns UNKNOWN_COMMAND).

### NOT_FOUND

* **Trigger.** `POST /reprint` with a `job_id` that doesn't exist
  (either never existed or was purged after `JOB_RETENTION_DAYS`).
* **HTTP.** `404`.
* **UI banner.** Yellow.

### GONE

* **Trigger.** `POST /reprint` with a job older than
  `REPRINT_MAX_AGE_HOURS` (default 24). The bytes still exist in the
  DB (until retention sweep) but reprint is **deliberately blocked** —
  prevents replaying a 7-day-old receipt that the customer no longer
  remembers.
* **HTTP.** `410`.
* **UI banner.** Yellow.

### RATE_LIMITED

* **Trigger.** Per-IP rate counter on `POST /print/*` exceeds
  `PRINT_RATE_LIMIT_PER_MIN` (default 10). Sliding window.
* **HTTP.** `429` with `Retry-After: <seconds>` header and
  `retry_after_s` in body.
* **UI banner.** Red, **sticky** (stays until next user action — the
  user clicked too many times and needs to see "wait 56s").
* **Recovery.** Wait. The window slides naturally.
* **Bypass.** Set `PRINT_RATE_LIMIT_PER_MIN=0` in `.env` to disable
  (RVM single-tenant case where the only client is trusted).

---

## State machine and error transitions

```
                 IDLE
                  │
                  │ (print starts)
                  ▼
              PRINTING
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
     DONE      ERROR     COMM_ERROR
                  │          │
       (recoverable: poll)   │ (reconcile loop)
                  │          ▼
                  ▼      RECONNECTING ── breaker_open ──> DISCONNECTED
                 IDLE
```

* Hardware errors (`PAPER_OUT`, `PAPER_JAM`, `COVER_OPEN`, `OVERHEAT`)
  push state to `ERROR` and the **polling loop** owns recovery (waits
  for the sensor bit to clear).
* `COMM_ERROR` pushes state to `ERROR` but the **reconcile loop** owns
  recovery (exponential backoff + breaker).
* `UNKNOWN_COMMAND` returns the error to the caller without touching
  state — it's not the device's fault.

---

## UI banner conventions

| Banner | Color | Auto-clear? | Used for |
|---|---|---|---|
| `ok` | green | yes (~1.5 s) | successful operations |
| `warn` | yellow | yes | non-fatal warnings |
| `error` (non-sticky) | red | when printer returns to IDLE | device fault detected by polling |
| `error` (sticky) | red | only when next user action overwrites it | action-result errors (rate limit, validation, reprint failures) |

The sticky variant exists specifically so that a fast-clicking user
sees their rate-limit message — without it, the next ~1.5 s status poll
overwrites the banner before the user has read it.

---

## Cross-references

* Status decoding details: [`escpos.md §4`](escpos.md#4-status-byte-decoding)
* Per-endpoint error response shape: [`api.md §error model`](api.md)
* Test coverage: [`test-summary.md`](test-summary.md) — see `test_error_policy.py`, integration error scenarios
