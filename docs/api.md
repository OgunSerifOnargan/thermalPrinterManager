# API Reference

All endpoints are JSON-in / JSON-out. Successful responses always carry
`ok: true`; errors carry `ok: false` plus
`{error_code, detail, message_tr, message_en, ts}`. All timestamps are
RFC 3339 / ISO-8601 in UTC unless explicitly the receipt wall-clock (see
`LOCAL_TIMEZONE` in [`operations.md`](operations.md)).

## Index

* [Liveness](#liveness)
  * [`GET /health`](#get-health) — shallow ping
  * [`GET /healthz`](#get-healthz) — deep probe
* [Connection](#connection)
  * [`POST /connect`](#post-connect)
  * [`POST /disconnect`](#post-disconnect)
  * [`GET /status`](#get-status)
* [Print](#print)
  * [`POST /print/text`](#post-printtext)
  * [`POST /print/image`](#post-printimage)
  * [`POST /reprint`](#post-reprint)
* [Logs](#logs)
  * [`GET /logs`](#get-logs)
  * [`POST /reprint/last-failed`](#post-reprintlast-failed)
  * [`GET /jobs/failed`](#get-jobsfailed)
  * [`GET /logs/export`](#get-logsexport)
* [Configuration](#configuration)
  * [`PATCH /config`](#patch-config)
* [Helpers / dev](#helpers--dev)
  * [`GET /assets/logo`](#get-assetslogo)
  * [`GET /preview/qr`](#get-previewqr)
  * [`GET /usb/devices`](#get-usbdevices)
  * [`GET /discover/usb`](#get-discoverusb)
  * [`GET /discover/lan`](#get-discoverlan)
  * [`GET /discover/cable`](#get-discovercable)
  * [`GET /mock/preview`](#get-mockpreview)
  * [`POST /mock/*`](#post-mock-mock-control)

---

## Liveness

### `GET /health`

Shallow process-up probe. Always 200 unless the FastAPI app is dead.
Suitable for L4 load-balancer health checks.

```json
{"ok": true, "service": "aco-printer", "version": "1.0.0",
 "ts": "2026-05-28T20:00:00Z", "dev_mode": true}
```

### `GET /healthz`

Deep liveness — verifies (a) SQLite responds to `SELECT 1`, (b) the
reconcile loop task is alive, (c) when `target_mode` is set, the transport
is connected and `last_seen_ts` is fresher than ~30 s.

* **200** — all sub-systems healthy.
* **503** — any sub-system degraded; body lists which.

Suitable for Kubernetes liveness/readiness and Docker `HEALTHCHECK`.

```bash
curl -i http://127.0.0.1:8000/healthz
```

Implementation: `app/api/routes.py`.

---

## Connection

### `POST /connect`

Set the target connection mode and open the link. Sets `target_mode`,
after which the reconcile loop keeps the link up.

**Request body** — `ConnectRequest`:

| Field | Type | Default | Notes |
|---|---|---|---|
| `mode` | `"usb" \| "lan" \| "lan_direct"` | required | Which transport to use. `lan_direct` triggers auto-discovery (see below). |
| `lan_host` | string | `.env LAN_HOST` | Per-request override for `mode=lan`. Ignored for `lan_direct`. |
| `lan_port` | int | `.env LAN_PORT=9100` | Ignored for `lan_direct`. |
| `usb_vendor_id` | string | `.env USB_VENDOR_ID` | Hex string (e.g. `0x0fe6`). |
| `usb_product_id` | string | `.env USB_PRODUCT_ID` | Hex string. |
| `usb_device_path` | string | `.env USB_DEVICE_PATH` | USB-CDC path (e.g. `/dev/cu.usbserial-…` or `/tmp/mock-printer-usb`). |

**Response — 200** (`mode=lan` / `mode=usb` → `ConnectResponse`):

```json
{"ok": true, "mode": "lan", "state": "idle",
 "ts": "2026-05-28T20:00:00Z"}
```

**Errors (`mode=lan` / `mode=usb`):**

* `503 COMM_ERROR` — transport open failed (LAN refused, USB not found).
* `422` — body validation failed (bad mode, malformed hex IDs).

**Curl:**

```bash
curl -X POST http://127.0.0.1:8000/connect \
  -H 'Content-Type: application/json' \
  -d '{"mode":"lan","lan_host":"127.0.0.1"}'
```

#### `mode=lan_direct` — one-tap direct-cable discovery

For the kiosk demo where the Cashino is plugged directly into the PC's
Ethernet port (no router in between), call:

```bash
curl -X POST http://127.0.0.1:8000/connect \
  -H 'Content-Type: application/json' \
  -d '{"mode":"lan_direct"}'
```

The service enumerates every active **wired** IPv4 interface via psutil
(loopback / bridge / AWDL / docker / Wi-Fi skipped), clamps each subnet
to `/24`, and runs the same TCP + `DLE EOT n=4` probe used by
`/discover/lan`. Outcomes:

* **200** — exactly one Cashino-shaped reply, auto-connected via the
  normal LAN path. Body:

  ```json
  {
    "ok": true,
    "mode": "lan_direct",
    "resolved": {
      "host": "169.254.42.50", "port": 9100,
      "via_interface": "en7", "rtt_ms": 4
    },
    "state": "idle",
    "ts": "2026-05-28T20:00:00Z"
  }
  ```

* **409 MULTIPLE_CANDIDATES** — more than one match. The body carries
  the full candidates list (each with `host`, `port`, `rtt_ms`,
  `status_byte`, `via_interface`) plus the localised user message.
  The UI renders this in a picker; the user clicks one and re-issues
  `/connect` with `mode=lan` + the picked host.

* **503 NO_DIRECT_DEVICE** — no Cashino-shaped reply on any wired
  interface. Body includes the scanned `interfaces` list so the user
  can verify the cable is in the right port. The localised message
  prompts the user to check the cable and retry.

Wall-clock is ~1-2 seconds (254-host /24 per interface, probed
concurrently). `lan_direct` always handles its own discovery — there
are no query params to tune the probe.

### `POST /disconnect`

Tear down the link, clear `target_mode`, and suppress auto-reconnect.

```json
{"ok": true, "state": "disconnected",
 "ts": "2026-05-28T20:00:00Z"}
```

### `GET /status`

Cached snapshot — never touches the device. Updated by the reconcile loop
every `POLL_INTERVAL_IDLE_MS` (or `POLL_INTERVAL_PRINTING_MS` during print).

**Response** (`StatusResponse`):

```json
{
  "ok": true,
  "ts": "2026-05-28T20:00:00Z",
  "printer_state": "idle",
  "connection": {
    "mode": "lan", "connected": true, "state": "idle",
    "target_mode": "lan", "disconnect_reason": null,
    "last_seen_ts": "2026-05-28T19:59:59Z"
  },
  "device": {
    "paper": "ok", "cover": "closed",
    "temperature_c": 32.5, "overheated": false, "jammed": false
  },
  "last_job": {
    "job_id": "8580b2d9-…", "op": "print_text",
    "status": "done", "error_code": null,
    "ts": "2026-05-28T19:55:14Z"
  },
  "activity": {"busy": false, "current_job_id": null, "pending_count": 0},
  "reconnect": null,
  "prediction": {
    "paper_consumed_mm": 124.0, "roll_length_mm": 10000,
    "remaining_mm": 9876.0, "avg_receipt_mm": 58.0,
    "estimated_receipts_remaining": 170
  },
  "eta": {"text_ms": 230, "image_ms": 280,
          "samples_text": 14, "samples_image": 3}
}
```

`device.paper` is `"ok" | "low" | "out"`. `device.cover` is
`"closed" | "open"`. `printer_state` is `"idle" | "printing" | "error" | "disconnected" | "connecting"`.

---

## Print

### `POST /print/text`

Render and print a receipt from JSON. Body schema (`PrintTextRequest`):

| Field | Type | Default | Notes |
|---|---|---|---|
| `machine_id` | string (1-64) | required | Printed in header. |
| `items` | `CategoryItem[]` (≤20) | `[]` | `{product, quantity, reward}`. |
| `total_reward` | float ≥ 0 | 0.0 | Printed in bold large text. |
| `timestamp` | datetime | server time | Rendered in `LOCAL_TIMEZONE`. |
| `qr_content` | string (≤500) | null | QR rendered via `GS ( k`. |
| `lang` | `"tr" \| "en"` | `"tr"` | Localized labels. |
| `idempotency_key` | string (≤128) | null | Race-safe dedup. |
| `title` | string (≤80) | null | Optional reward title. |

`CategoryItem`:

| Field | Type | Notes |
|---|---|---|
| `product` | string (1-40) | Category label. |
| `quantity` | int ≥ 0 | |
| `reward` | float ≥ 0 | TL value. |

**Response — 200** (`PrintResponse`):

```json
{"ok": true, "job_id": "8580b2d9-…",
 "status": "done", "error_code": null,
 "idempotent_replay": false, "duration_ms": 234,
 "ts": "2026-05-28T20:00:00Z"}
```

**Errors:**

* `503` — device fault (PAPER_OUT, PAPER_JAM, COVER_OPEN, OVERHEAT, COMM_ERROR).
* `400` — UNKNOWN_COMMAND (e.g. rendered payload exceeds `MAX_RECEIPT_BYTES`).
* `422` — body validation (missing field, too long, etc.).
* `429` — RATE_LIMITED (per-IP `PRINT_RATE_LIMIT_PER_MIN` exceeded).

**Curl:** see [README §5.3](../README.md#53-curl-examples).

### `POST /print/image`

Identical to `/print/text` but the request body extends with one extra
field:

| Field | Type | Default | Notes |
|---|---|---|---|
| `image_base64` | string | required | PNG / JPEG / BMP / GIF, base64-encoded; raw bytes must be ≤ `MAX_IMAGE_BYTES`. |

Image is auto-fitted to `PAPER_WIDTH_COLS` and dithered to 1-bpp before
ESC/POS raster encoding. Aspect ratio preserved; max source resolution
~1500 × 3000 px (anything larger is downscaled).

### `POST /reprint`

Replay a stored job by `job_id`. Bytes (ESC/POS payload) are the same as
the original — even if the renderer code has since changed, output is
byte-for-byte identical.

**Request:** `ReprintRequest`:

| Field | Type | Notes |
|---|---|---|
| `job_id` | string (1-64) | Required. |
| `idempotency_key` | string | Optional dedup of repeated reprint calls. |

**Response:** `PrintResponse` (same shape as print).

**Errors:**

* `404 NOT_FOUND` — `job_id` doesn't exist (or was purged after
  `JOB_RETENTION_DAYS`).
* `410 GONE` — job exists but is older than `REPRINT_MAX_AGE_HOURS`.
* `503` — device fault.

**Special non-error response — `status: "already_done"`.** A reprint
request whose target UUID belongs to a job that **already succeeded**
returns `200` with this status instead of producing a duplicate
receipt. The `/reprint` contract is *recover a failed job*; a DONE
uuid is most likely a typo or a wrong line picked from the logs, and
silently double-printing would burn paper for no reason.

```json
{
  "ok": true,
  "job_id": "8580b2d9-…",        // original uuid, no new job created
  "status": "already_done",
  "error_code": null,
  "idempotent_replay": false,
  "duration_ms": 234,             // surfaces the original print's duration
  "ts": "2026-05-28T20:00:00Z"
}
```

The UI banners this as an informational message ("this job was already
printed successfully — no reprint needed"). Callers that want
unconditional replay should rely on the byte-identical replay
guarantee that already holds for `status: "error"` jobs (the original
failure path), or back the operation out manually before hitting
`/reprint`. The new endpoint sibling `POST /reprint/last-failed`
filters on `ERROR` by construction, so it never triggers
`already_done`.

### `POST /reprint/last-failed`

Convenience sibling of `/reprint` — no UUID required. The service
looks up the most recently failed job inside the reprint TTL window
and replays it. Designed for scriptable / curl callers that don't
track job IDs themselves and for the UI "Reprint last failed" button.

**Request:** empty body. (Sending one is ignored.)

**Response:** `PrintResponse` (same shape as `/reprint`) — the
`job_id` in the response is the **new** reprint job, not the original
failure ID.

**Errors:**

* `404 NO_FAILED_JOB` — no eligible failed job inside the TTL window.
  Distinct from `/reprint`'s `404 NOT_FOUND` (which means "the UUID
  you supplied doesn't exist") — `NO_FAILED_JOB` means "the queue
  has nothing to reprint right now".
* `503` — device fault.

**Curl:**

```bash
curl -X POST http://127.0.0.1:8000/reprint/last-failed
```

**Design note.** A separate endpoint instead of overloading `/reprint`
with a sentinel `job_id` value (e.g. `"last_failed"`) because:

* `job_id` stays strictly UUID-typed — no magic strings.
* "Reprint this specific job" and "reprint the queue's last failed"
  are semantically different resources; distinct URIs match REST
  intent.
* Each carries its own error model — `NO_FAILED_JOB` is reserved for
  this endpoint and doesn't pollute the `/reprint` 404 path that
  callers handle as "I sent a bad ID".

### `GET /jobs/failed`

List of recent ERROR-status jobs still inside the reprint TTL window.
Powers the UI's "recent failures" dropdown so the user can pick a
specific UUID to retry without copy/pasting from the logs view, but
also useful from scripts ("show me what's broken lately").

Query params:

| Param | Default | Range | Notes |
|---|---|---|---|
| `limit` | `5` | 1–50 | Out-of-range → 422 |

Response — always 200 (empty list when nothing failed, never 404 — the
UI calls this on page load and wants to render an empty picker, not
handle an error):

```json
{
  "ok": true,
  "limit": 5,
  "count": 2,
  "ttl_hours": 24,
  "jobs": [
    {
      "job_id": "8580b2d9-…",
      "op_type": "print_text",
      "error_code": "PAPER_OUT",
      "error_detail": "device fault before print: PAPER_OUT",
      "ts": "2026-05-28T19:55:14Z"
    },
    {
      "job_id": "…",
      "op_type": "print_image",
      "error_code": "COVER_OPEN",
      "error_detail": "...",
      "ts": "2026-05-28T19:42:01Z"
    }
  ]
}
```

**What's *not* in the response:** `machine_id`, `items`, `total_reward`,
the raw `payload_bytes`. These are redacted at log-write time (see
[`operations.md §3.2`](operations.md#32-pii-scrub)) and exposing them
here would defeat that. The fields shown are operational metadata only.

**Curl:**

```bash
curl http://127.0.0.1:8000/jobs/failed
curl "http://127.0.0.1:8000/jobs/failed?limit=20"
```

---

## Logs

### `GET /logs`

Recent JSONL events from `LOG_DIR/service.jsonl`.

**Query params:**

* `level` — minimum level (`DEBUG | INFO | WARNING | ERROR`); default `INFO`.
* `limit` — max records (default 100, max 1000).
* `op` — filter by op name (e.g. `print_text`, `connect`, `rate_limit`).

**Response:**

```json
{"ok": true, "count": 2, "events": [
  {"ts":"2026-05-28T20:00:00Z","level":"info","op":"print_text",
   "job_id":"8580b2d9-…","status":"done","duration_ms":234},
  {"ts":"2026-05-28T19:55:14Z","level":"warning","op":"rate_limit",
   "client":"127.0.0.1","retry_after_s":56}
]}
```

Sensitive fields (`qr_content`, `machine_id`, `items`, `total_reward`) are
redacted as `[REDACTED]` at log-write time — see
[`operations.md`](operations.md#logging-and-pii-scrub).

### `GET /logs/export`

Stream the full log as a downloadable file. Query: `?format=csv` (or
`?format=jsonl`, default).

Headers set: `Content-Disposition: attachment; filename=service-…csv`.

---

## Configuration

### `PATCH /config`

Token-guarded, **atomic** runtime tune of a whitelisted subset. Either
all updates apply or none.

**Auth:** when `CONFIG_PATCH_TOKEN` is non-empty, requests must include
`Authorization: Bearer <token>`. Empty token disables auth (dev mode).

**Body** — `ConfigPatchRequest` (all fields optional, any combination):

| Field | Range |
|---|---|
| `poll_interval_idle_ms` | 50 – 60000 |
| `poll_interval_printing_ms` | 10 – 10000 |
| `reprint_max_age_hours` | 1 – 720 |
| `paper_low_threshold_mm` | 0 – 10000 |
| `breaker_threshold` | 1 – 100 |
| `backoff_factor` | 1.0 – 10.0 |
| `use_lira_symbol` | bool |
| `log_level` | `"DEBUG" \| "INFO" \| "WARNING" \| "ERROR"` |

**Response — 200** (`ConfigPatchResponse`):

```json
{"ok": true, "updated": {"poll_interval_idle_ms": 250},
 "ts": "2026-05-28T20:00:00Z"}
```

**Errors:**

* `401` — missing/bad Bearer token.
* `422` — any field out of range → **no** field is applied (atomic).

---

## Helpers / dev

### `GET /assets/logo`

Returns the configured PNG that the printer renders at the top of each
receipt. The UI uses it in the preview pane and as the logo upload
fallback. 404 if no logo is configured.

### `GET /preview/qr`

UI helper, not an asset — generates a PNG QR code from the `?data=`
query param on the fly. The actual receipt's QR is rendered by the
printer firmware itself via `GS ( k`; this endpoint exists so the
browser preview pane can show what the QR encodes before the customer
takes the receipt.

Query params:

* `data` (required) — string to encode.
* `box` (optional, default 8, clamped to 2-16) — pixel size per QR module.

```bash
curl "http://127.0.0.1:8000/preview/qr?data=KIOSK-01|TX-9876|3.00|TL" \
  --output preview.png
```

### `GET /usb/devices`

OS-discovered USB-CDC paths and libusb VID/PID matches — populates the UI's
USB device dropdown. Linux/macOS scan `/dev/cu.*` / `/dev/ttyACM*`;
Windows scans COM ports.

Response:

```json
{
  "serial_ports": [
    {"path": "/dev/cu.usbserial-A1B2C3", "label": "...",
     "manufacturer": "Cashino", "product": "KP-302",
     "vid_hex": "0x0fe6", "pid_hex": "0x811e",
     "serial_number": "...", "is_mock": false}
  ],
  "libusb_devices": [
    {"vendor_id": "0x0fe6", "product_id": "0x811e",
     "manufacturer": "Cashino", "product": "...", "bus": 1, "address": 4}
  ]
}
```

### `GET /discover/usb`

Alias of [`GET /usb/devices`](#get-usbdevices) — same body, different
path. Exists for symmetry with `/discover/lan` so a generic discovery
client can call both without knowing the legacy name.

### `GET /discover/lan`

Subnet sweep — scans the host's local `/24` for Cashino-shaped TCP
listeners. The two-step probe per host is:

1. **TCP connect** on `port` (default 9100, the ESC/POS standard).
   If the port isn't accepting, the host is dropped from the result
   set entirely.
2. **Protocol probe** — send `DLE EOT n=4` (`10 04 04`) and read 1
   byte. A Cashino-shaped reply has reserved bits 1 + 4 set and
   bits 0 + 7 clear (`(byte & 0x93) == 0x12`). Anything else falls
   into `looks_like_cashino: false`.

The local IP is detected via a UDP-connect trick that picks the
kernel's egress interface without sending any packets — works across
multi-NIC / VPN setups without ifconfig parsing. If detection fails
(no usable route, e.g. airplane mode), the response carries
`local_ip: null` and `reason: "local_ip_undetected"`.

Wall-clock latency is roughly **1-2 seconds** because the 254 hosts
are probed concurrently via `asyncio.gather`.

Query params:

| Param | Default | Notes |
|---|---|---|
| `port` | `9100` | TCP port probed on every host. |
| `strict` | `true` | When `true`, only hosts whose reply matched the Cashino bit pattern are returned. `false` returns every TCP-open host (debug "what's on my network" mode). |

Response:

```json
{
  "local_ip": "192.168.1.20",
  "subnet": "192.168.1.0/24",
  "port": 9100,
  "scanned": 253,
  "tcp_open": 2,
  "candidates": [
    {"host": "192.168.1.50", "port": 9100,
     "rtt_ms": 12, "looks_like_cashino": true,
     "status_byte": "0x12"}
  ]
}
```

Curl:

```bash
# Strict — confirmed Cashino-shaped only:
curl http://127.0.0.1:8000/discover/lan

# Loose — every TCP-open host (debugging):
curl "http://127.0.0.1:8000/discover/lan?strict=false"

# Non-default port:
curl "http://127.0.0.1:8000/discover/lan?port=9101"
```

**Caveats.**
* Subnet width is assumed to be `/24`. On networks using `/16`, `/22`,
  or otherwise non-standard CIDRs, the sweep covers only the /24
  around the host's IP. Manual `/connect` with the explicit host is
  the fallback.
* The probe is **non-destructive** but does open a brief TCP
  connection to every host on the subnet. On managed networks this
  may register on IDS / firewall logs as a small port scan.
* The endpoint is **unauthenticated** by design (it's a local
  diagnostic, not a remote-callable feature). When exposing the
  service to a broader network, front it with auth.

### `GET /discover/cable`

Read-only sibling of `POST /connect {"mode":"lan_direct"}` — runs the
same wired-interface scan and returns the candidates, but **does not
connect**. The UI calls this when the user picks "LAN — auto-detect
direct cable" in the Mode dropdown so the candidates can be rendered
in a picker.

When the service runs with `DEV_MODE=true`, a synthetic loopback
interface is included in the scan so the local mock device shows up
in the list — letting a developer rehearse the lan_direct UX end-to-end
without an actual cable.

Query params:

| Param | Default | Notes |
|---|---|---|
| `port` | `9100` | TCP port probed on every host. |

Response — same shape as the `discover_cable` body inside `mode=lan_direct`:

```json
{
  "interfaces": [
    {"iface": "en7", "ip": "192.168.1.20", "subnet": "192.168.1.0/24"},
    {"iface": "dev-mock-cable", "ip": "127.0.0.255",
     "subnet": "127.0.0.0/29"}
  ],
  "port": 9100,
  "scanned": 260,
  "tcp_open": 1,
  "candidates": [
    {"host": "127.0.0.1", "port": 9100, "rtt_ms": 1,
     "looks_like_cashino": true, "status_byte": "0x12",
     "via_interface": "dev-mock-cable"}
  ]
}
```

```bash
curl http://127.0.0.1:8000/discover/cable
```

### `GET /mock/preview`

DEV-only (requires `DEV_MODE=true`). Returns a text dump of the latest
job's ESC/POS bytes — useful for verifying renderer output without the
printer. Optional `?job_id=…` to inspect a specific job.

```json
{"preview": "[INIT][CP857][CENTER]Aco Recycling…",
 "bytes_len": 7316}
```

### `POST /mock/*` (mock control)

DEV-only. When the in-process mock is the active transport, these inject
state for testing. When the standalone mock is running (compose / PTY
setup), the service proxies to `MOCK_DEVICE_CONTROL_URL`.

| Endpoint | Body |
|---|---|
| `POST /mock/set_paper` | `{"lines": 41}` |
| `POST /mock/set_cover` | `{"open": true}` |
| `POST /mock/set_jammed` | `{"jammed": true}` |
| `POST /mock/set_temperature` | `{"celsius": 72.0}` |
| `POST /mock/set_comm_error` | `{"active": true}` |
| `POST /mock/run_scenario` | `{"scenario": "paper_out", "auto_recover_after_ms": 3000}` |

`scenario` is one of `paper_out | cover_open | jam | overheat | comm_drop | recover_all`. With `auto_recover_after_ms`, the brain clears the fault
after the given delay — useful for "show the breaker reopen" demos.

---

## Cross-references

* Field-level error model: [`errors.md`](errors.md)
* When to use which command (DLE EOT vs GS r 1): [`escpos.md`](escpos.md)
* Test coverage per endpoint: [`test-summary.md`](test-summary.md)
