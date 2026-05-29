# Deployment & Operations Guide

How to run, observe, secure, and recover this service in production-ish
environments. Targeted at the RVM (single-tenant) deployment; multi-RVM
scale-out is in [`limitations.md`](limitations.md).

## 1. Deployment options

### 1.1 Docker compose (recommended for demo / interview)

```bash
docker compose up -d
```

Brings up two containers:

* `aco-mock` — simulated Cashino on TCP 9100 + control API on 9101.
* `aco-service` — FastAPI app on 8000, pointed at `mock-device` over
  compose DNS.

Persistent state lives in host-mounted volumes:

```
./logs    → /app/logs   (JSONL service logs; rotated daily, 7-day retention)
./data    → /app/data   (jobs.db SQLite with WAL)
```

### 1.2 Docker, real Cashino over LAN

```yaml
# docker-compose.yml — comment out the mock-device service and set:
service:
  environment:
    LAN_HOST: 192.168.1.50   # printer IP
    LAN_PORT: "9100"
    DEFAULT_MODE: lan
```

Or run a one-off:

```bash
docker build -t aco-service .
docker run --rm -p 8000:8000 \
  -e TRANSPORT_BACKEND=real -e DEFAULT_MODE=lan \
  -e LAN_HOST=192.168.1.50 -e LAN_PORT=9100 \
  -v $(pwd)/logs:/app/logs -v $(pwd)/data:/app/data \
  aco-service
```

### 1.3 From source (macOS / Linux)

```bash
./setup.sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

USB transport works only from source (Docker on macOS/Windows lacks USB
passthrough). On Linux, USB inside Docker works with `--device`.

---

## 2. Configuration reference

All settings live in `.env`; `app/core/config.py` validates them at
startup and **fails fast** (the process exits with a clear error) rather
than silently misbehaving. Each setting is documented inline in
[`.env.example`](../.env.example). The most operationally relevant:

| Variable | Default | Effect |
|---|---|---|
| `TRANSPORT_BACKEND` | `mock` | `mock` = in-process brain, `real` = LAN/USB. |
| `DEFAULT_MODE` | empty | `usb`/`lan`/empty. Empty = service starts DISCONNECTED, user must `/connect`. |
| `LAN_HOST`, `LAN_PORT` | -, 9100 | LAN target. |
| `USB_DEVICE_PATH` | empty | USB-CDC path (e.g. `/dev/cu.usbserial-XXXX`). Takes precedence over VID/PID. |
| `CONNECT_RETRY_BASE_MS`, `CONNECT_RETRY_MAX_MS`, `BACKOFF_FACTOR` | 500, 30000, 2.0 | Exponential backoff for reconnect. |
| `BREAKER_THRESHOLD` | 5 | Consecutive failures before the breaker opens. |
| `POLL_INTERVAL_IDLE_MS`, `POLL_INTERVAL_PRINTING_MS` | 1000, 200 | Background poll cadence. |
| `OVERHEAT_STOP_C`, `OVERHEAT_RESUME_C` | 65, 55 | Cashino KP-302 thresholds. KP-301H uses 60 for resume. |
| `PAPER_WIDTH_COLS` | 32 | 32 = 58 mm paper, 48 = 80 mm. |
| `PRINTER_CODEPAGE` | `cp857` | Turkish DOS. |
| `LOCAL_TIMEZONE` | `Europe/Istanbul` | Receipt wall-clock. Logs stay UTC. |
| `WAIT_FOR_PRINT_DONE` | `true` | Use `GS r 1` fence to detect print-done. |
| `WAIT_FOR_PRINT_TIMEOUT_MS` | 5000 | Fence read timeout. |
| `MAX_RECEIPT_BYTES` | 30720 | Rendered payload hard cap (protects printer buffer). |
| `MAX_IMAGE_BYTES` | 2097152 | 2 MB image upload cap. |
| `REPRINT_MAX_AGE_HOURS` | 24 | Reprint TTL. After this → 410 Gone. |
| `JOB_RETENTION_DAYS` | 7 | Sliding-window hard-delete of old jobs. Must be ≥ reprint TTL in days. |
| `CONFIG_PATCH_TOKEN` | empty | Empty disables auth on `PATCH /config`. |
| `PRINT_RATE_LIMIT_PER_MIN` | 10 | Per-IP rate cap on `POST /print/*`. 0 = disabled. |
| `LOG_LEVEL` | `INFO` | `DEBUG / INFO / WARNING / ERROR`. |
| `LOG_RETENTION_DAYS` | 7 | JSONL log rotation window. |
| `DEV_MODE` | `true` | `true` exposes `/mock/*` endpoints + UI Dev Tools card. **Set to `false` in production.** |

### Runtime tuning

A whitelisted subset of these can be patched at runtime via
`PATCH /config` — see [`api.md`](api.md#patch-config). Patches are
**atomic** (all-or-nothing) so a single bad field doesn't half-apply.

---

## 3. Logging and PII scrub

### 3.1 Format

Structured JSONL, one event per line:

```json
{"ts":"2026-05-28T20:00:00.123Z","level":"info",
 "logger":"app.services.printer_service",
 "message":"print done","op":"print_text",
 "job_id":"8580b2d9-…","status":"done","duration_ms":234}
```

* `ts` — UTC, milliseconds.
* `op` — operation name (`print_text`, `print_image`, `reprint`,
  `connect`, `disconnect`, `poll`, `rate_limit`, `startup`, `shutdown`,
  `usb_open`, etc.).
* `level` — `info | warning | error`.

### 3.2 PII scrub

`JsonlFormatter._scrub` redacts these keys before writing:

* `qr_content` — may contain user-identifiable transaction IDs.
* `machine_id` — RVM identifier.
* `items` — full category list.
* `total_reward` — TL amount.

Redacted as `"[REDACTED]"`. Service metadata (`job_id`, `duration_ms`,
`error_code`, transport mode) stays — operational visibility is
preserved. KVKK / GDPR compatible by default.

### 3.3 Rotation

The logger writes to `LOG_DIR/service.jsonl` and rotates daily.
`LOG_RETENTION_DAYS` files are kept, older ones are deleted. For higher
throughput RVMs, switch to `logrotate` and disable internal rotation.

### 3.4 CSV export

`GET /logs/export?format=csv` streams the full log as a CSV download
with a filename hint. Useful for ad-hoc analysis in Excel/Sheets.

---

## 4. Healthcheck

Two endpoints with different semantics:

| Endpoint | What it checks | Use for |
|---|---|---|
| `/health` | FastAPI app is up and serving | L4 LB checks, basic liveness |
| `/healthz` | DB ping + reconcile-loop alive + link freshness | Container `HEALTHCHECK`, Kubernetes readiness |

`/healthz` returns **503** when any sub-system is degraded, with the
response body indicating which. Both `Dockerfile` and `Dockerfile.mock`
use `/healthz` (and `/9100 TCP accept`, respectively) as their
HEALTHCHECK target.

---

## 5. Security

### 5.1 Endpoints

| Endpoint | Auth |
|---|---|
| `PATCH /config` | Bearer token (`CONFIG_PATCH_TOKEN`) when set; open in dev |
| `POST /mock/*` | DEV-only — gated by `DEV_MODE=true` |
| Everything else | unauthenticated (RVM local network) |

For an RVM on an isolated internal network, this is appropriate. For a
networked deployment, front the service with a reverse proxy enforcing
mTLS or an OAuth gateway.

### 5.2 Rate limiting

`POST /print/*` is rate-limited per-IP via `app/core/rate_limit.py`
(sliding window). Default 10/min. On hit, the response is `429` with
`Retry-After` set. UI banner becomes sticky so the user sees the wait
time.

### 5.3 Receipt size cap

Defensive: `MAX_RECEIPT_BYTES=30720` rejects monster receipts before
they reach the printer's small (4-64 KB) receive buffer. Anything
beyond this returns `UNKNOWN_COMMAND` rather than silently overflowing.

### 5.4 Image size cap

`MAX_IMAGE_BYTES=2097152` (2 MB) — pre-decode hard cap. Even legitimate
photos are downscaled to the paper width before raster encoding, so 2 MB
is generous.

### 5.5 PII scrub

See [§3.2](#32-pii-scrub).

---

## 6. Job persistence and retention

### 6.1 Storage

SQLite database at `JOBS_DB_PATH` (default `./jobs.db` or
`/app/data/jobs.db` in Docker). WAL mode for concurrent reads during
writes. Schema lives in `app/services/job_repository.py`.

Each row:

* `job_id` — UUID v4
* `op` — `print_text | print_image | reprint`
* `idempotency_key` — UNIQUE; `INSERT OR IGNORE` for race-safe dedup
* `payload_bytes` — BLOB of the rendered ESC/POS payload (so reprint
  produces byte-identical output)
* `status` — `PRINTING | DONE | ERROR`
* `error_code`, `error_detail`, `duration_ms`, `created_ts`, `done_ts`

### 6.2 Reprint TTL

`REPRINT_MAX_AGE_HOURS` (default 24). Older → 410 Gone. The bytes still
exist in the DB until retention runs.

### 6.3 Retention sweep

Two cleanup paths:

* **On startup** — `JobRepository.purge_older_than(JOB_RETENTION_DAYS)`
  hard-deletes everything older than the window.
* **Hourly housekeeping task** — same call, prevents the DB from growing
  unbounded between restarts.

`JOB_RETENTION_DAYS` must be ≥ `REPRINT_MAX_AGE_HOURS / 24`; the config
validator enforces this.

### 6.4 Orphan recovery on restart

If the service crashes between `send(payload)` and `mark_print_done`, a
job's row stays at `status=PRINTING` forever. On startup,
`JobRepository.list_orphan_printing()` marks all such rows
`status=ERROR error_code=COMM_ERROR error_detail="service restart"` so
the UI doesn't show "printing forever" and reprint behavior is sane.

---

## 7. Restart playbook

1. **Graceful shutdown.** `SIGTERM` → FastAPI lifespan cancels the
   reconcile loop, the housekeeping task, closes the transport, flushes
   logs. SQLite WAL checkpoint runs.
2. **Restart.** Lifespan startup runs orphan sweep + initial retention
   purge before opening the API port. New connections are accepted only
   once the DB and reconcile loop are ready.
3. **What you'd see.** UI immediately reads `/status`; if `DEFAULT_MODE`
   is set the reconcile loop has already started reconnecting. Otherwise
   user clicks Connect.

No manual intervention is needed for a clean restart. For a hard crash,
the orphan sweep is what makes state consistent again.

---

## 8. Backup

The service does not produce its own backups. Recommended approach:

* **`logs/`** — already rotated; cron `rsync` to durable storage if
  audit logs matter.
* **`data/jobs.db`** — SQLite. Use `sqlite3 jobs.db ".backup
  jobs.backup.db"` for a consistent online backup. With WAL mode, the
  file copy alone may miss in-flight writes; `.backup` is safer.
* **`.env`** — version-control the deployment-specific values in a
  secure config repo (without the token).

`integrity_check` runs implicitly on every SQLite open; if it fails, the
service exits at startup with a clear message rather than silently
losing rows.

---

## 9. Troubleshooting

### 9.1 "PAPER_JAM" returned at low paper, but the mock shows no jam

This was a real bug — accidental `10 04` byte sequences inside QR/raster
data were misinterpreted as DLE EOT commands by the naïve mock parser,
producing spurious status responses that polluted later legitimate
reads. **Fixed** by the ESC/POS-aware stateful parser
(`_EscPosScanner`). If you see this on a real Cashino, check the
forensic log:

```bash
grep "device error" logs/service.jsonl | tail
```

The `raw_n2 / raw_n3 / raw_n4` fields tell you the exact bytes the
decoder saw — misalignment is one byte log away.

Detailed write-up: [`escpos.md §5`](escpos.md#5-the-paper_jam-case-study).

### 9.2 Service starts but `/status` reports `connected: false`

Most common: `DEFAULT_MODE` is empty (intentional — service won't
auto-connect at boot). Click Connect in the UI or POST `/connect`.

If `DEFAULT_MODE` is set and still no connection, check
`/logs?op=connect` for the actual transport error (LAN refused, USB not
found).

### 9.3 Print returns `done` but nothing comes out the printer

If `WAIT_FOR_PRINT_DONE=false`, the service returns immediately after
`send()` — paper feeding isn't waited for. Re-enable the fence
(`WAIT_FOR_PRINT_DONE=true`). For real devices the fence response
arrives ~10-15 ms per line; expect a 200-500 ms print to take that long
end-to-end.

### 9.4 Receipt is too long → `UNKNOWN_COMMAND: receipt too large`

The rendered ESC/POS payload exceeded `MAX_RECEIPT_BYTES` (default 30
KB, protecting the printer's 4-64 KB buffer). Reduce items, or raise
the cap if your printer's buffer is larger.

### 9.5 Rate-limit hits when you expect it not to

`PRINT_RATE_LIMIT_PER_MIN=10` per IP. Demo clicking 10 prints in a
minute will hit the wall on the 11th. Either wait, set the env var to
0 (disabled), or PATCH it at runtime via `/config`.

### 9.6 Mock device dashboard at :9101 doesn't load

Container's not healthy — `docker compose ps` shows `unhealthy`. Check
`docker compose logs mock-device`. Most common cause: the build context
omitted required files; rebuild with `docker compose build --no-cache
mock-device`.

### 9.7 `/discover/lan` returns empty candidates

A few cases the endpoint handles explicitly:

* **`local_ip: null` + `reason: "local_ip_undetected"`** — the host
  has no usable network route. Common in containers without an
  external network, on airplane mode, or when every NIC is down.
  Fall back to manual `/connect` with the host you know.
* **`local_ip` is set, `tcp_open: 0`** — the discovery actually ran
  but no host on the /24 accepted TCP on the probed port. The
  printer might be on a different subnet (see below), unplugged, or
  using a non-9100 port (try `?port=NNNN`).
* **`tcp_open > 0`, `candidates: []` with `strict=true`** — something
  is listening on 9100 but its reply byte didn't match the
  Cashino-shaped bit pattern. Re-run with `?strict=false` to see
  every TCP-open host (returns the raw status byte so you can verify
  yourself).

**Wider subnet than /24.** The endpoint assumes a `/24` around the
host's IP. On a `/16` or `/22` network the printer may simply be
outside the scanned range. Either run discovery from a machine on the
same `/24` as the printer, or skip discovery and pass the host
directly to `/connect`.

**`/discover/lan` vs `mode=lan_direct`.** The endpoint relies on a
**default route** (the kernel UDP-trick that picks an egress
interface). On a host with no internet — including the common
"printer plugged straight into the Ethernet port" demo — there is no
default route and discovery returns empty. For that case use
`POST /connect {"mode":"lan_direct"}` instead: it iterates *every
active wired interface* and probes each one's /24 directly, without
asking the kernel for a default route.

### 9.8 `lan_direct` — direct-cable troubleshooting

`POST /connect {"mode":"lan_direct"}` covers the kiosk demo: printer
plugged straight into the PC's Ethernet port, no router in between.
When it returns 503 `NO_DIRECT_DEVICE`, walk through:

* **Is the cable seated and is the link LED on** at the PC and the
  printer? A loose RJ-45 latch is the most common cause.
* **Is the PC's interface even listed?** Check the body of the 503
  response — it carries the `interfaces` array of what the scanner
  saw. If the cable port isn't in that list, the OS hasn't brought
  it up yet (assigned no IPv4). On macOS / Linux, an `ifconfig` /
  `ip addr show` will tell you; on Windows, `ipconfig /all`.
* **Did the printer self-test print a non-`192.168.x.x` IP?** Some
  Cashino units default to a static IP outside the typical APIPA
  range; if it's, say, `10.0.0.50`, the PC's link-local /24 won't
  reach it. Set the PC's interface to a static IP on the same
  subnet, or fall back to manual `mode=lan` with the documented IP.
* **Multiple Ethernet adapters in the same machine?** The scanner
  probes all of them in parallel — the printer should be on
  whichever cable matches its self-test IP. A response with
  `via_interface: "en9"` (for example) tells you exactly which
  physical port answered.

---

## 10. Operations checklist (pre-launch)

* [ ] `DEV_MODE=false` in production .env.
* [ ] `CONFIG_PATCH_TOKEN` set to a non-empty value.
* [ ] `PRINT_RATE_LIMIT_PER_MIN` matched to expected load (RVM is low — 10 is fine).
* [ ] `LOG_DIR` mounted on durable storage with sufficient inodes (~700 MB/year).
* [ ] `data/` volume mounted on durable storage; `jobs.db` backup cron in place.
* [ ] Printer reachable on `LAN_HOST` from inside the service container.
* [ ] Healthcheck visible from your orchestrator (compose `healthy`, k8s
  `Ready`).
* [ ] First print smoke test passes from a real client.

---

## Cross-references

* Config knobs reference: [`.env.example`](../.env.example)
* Architecture & sequence diagrams: [`architecture.md`](architecture.md)
* Demo run-book: [`demo_checklist.md`](demo_checklist.md)
* Bonus / deliberate-exclusion list: [`limitations.md`](limitations.md)
