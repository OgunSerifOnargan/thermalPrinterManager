# Limits & Deliberate Exclusions

This service was scoped to a two-week deliverable for an RVM. Below are
the things I knowingly left out, why, and what would change them.

> The hardening items **G1-G8** referenced at the bottom of this file
> have all been implemented in this codebase. The remaining sections
> below are the things deliberately **not** done — what we would add
> next, with rationale.

## PTY USB-CDC simulation is host-only (not in Docker)

The mock device's `--enable-pty` flag creates a pseudo-terminal at
`/tmp/mock-printer-usb` so the same `SerialTransport` code path can drive
the mock without a real Cashino. PTYs are host-kernel resources and
don't survive container boundaries cleanly. In the Docker compose
demo, the mock exposes TCP 9100 only and the service uses LAN
transport (`LAN_HOST=mock-device`). USB-CDC simulation requires the
from-source setup.

## OVERHEAT recovery deterministic only in tests
The mock printer exposes a `tick_time(seconds)` hook that fast-forwards the
heat / cool model, so the full PAPER_OUT → OVERHEAT → resume cycle runs
in milliseconds in CI. On a real Cashino KP-302 the head needs ~2 minutes
to drop from 66 °C to 55 °C; we trust the datasheet thresholds and surface
the auto-recovery in status rather than blocking on it. To prove it live
you'd have to print ~50 receipts in rapid succession.

## USB on Windows requires Zadig
`pyusb` needs a libusb-compatible driver. macOS gets it from `brew install
libusb`; Linux from kernel + udev; Windows needs the user to bind WinUSB
or libusb-win32 to the printer via [Zadig](https://zadig.akeo.ie). There
is no programmatic way to automate this safely from PowerShell. `setup.ps1`
sets up the Python side; the driver step lives in the README.

## Image upload capped at 2 MB
Larger images get a 422 from `MAX_IMAGE_BYTES`. Reasons: (a) the printer's
raster path is 384 dots wide × however tall — most logos are <50 KB after
1-bit dithering, so 2 MB is a generous ceiling; (b) prevents a trivial OOM
vector where a multi-megabyte base64 payload arrives in JSON.

## Reprint TTL 24 hours
Configured by `REPRINT_MAX_AGE_HOURS`. Beyond the window the original
job → 410 Gone. This avoids surprises like reprinting a refund-receipt
from a day-old shift. Bump the value if a longer window is required.

## `paper_consumed_mm` is in-memory and conservative
The estimate lives on `PaperPredictor`; it resets on (a) service restart,
and (b) any out→ok transition reported by the device sensor. Failed prints
are counted as full receipts so the warning fires early rather than late.
Never used as ground truth — the actual `paper_lines==0` sensor reading
always wins.

## No CORS, no multi-tenant queue
RVM is single-client (the kiosk software talks to localhost) and single-job
(one customer at a time). Adding either is a clean extension via a
queue + per-customer auth, but neither is in the requirement.

## Mock UI replaced by HTTP endpoints + a "Dev Tools" panel inside the main UI
A dedicated `/mock-ui` page was on the wishlist but adds ~1.5 days of UI
work for marginal demonstration value. The HTTP endpoints are scriptable
from Postman / curl, and the in-UI Dev Tools panel (only visible when
`DEV_MODE=true`) gives the same slider / scenario experience without
duplicating UI infrastructure.

## No demo video bundled
The mock + Dev Tools panel makes the demo reproducible in a browser; a
recorded Loom can be added in ~1 hour if the interview requires it.

## Things python-escpos does not give us (so we did them ourselves)
- A code-page profile for Cashino KP-300/301H/302 (the library has none).
  We bypass `magicencode` and write our own `cp857` encoder with
  transliteration fallback (`ş→s`, `₺→TL`, etc.) so the print path never
  raises and always produces printable bytes.
- Robust `DLE EOT n` status reading. We send the bytes ourselves and parse
  the four registers (n=1..4) into a structured `DeviceStatus`.

---

## Operational hardening — what we did NOT add (and why)

The service ships hardening for the eight items we judged worth the time
(see the `G1–G8` audit in `/Users/.../plans/...md`). The rest are sized
larger than the two-week scope; below is what we'd add next and the
rationale for leaving them out today.

### Prometheus `/metrics` endpoint
JSONL logs + `GET /logs/export?format=csv` are enough for a single-RVM
deployment. For a fleet (10+ RVMs), we'd expose five metrics: `prints_total`,
`print_duration_ms`, `paper_consumed_mm`, `errors_by_code`,
`connection_uptime_seconds`. ~3 hours of work; the logging hooks are
already in place to feed them.

### Database backup / corruption recovery
SQLite is in WAL mode, but no cron backup is shipped. We rely on the
operator (ops responsibility) plus a startup `PRAGMA integrity_check`
that would let us recreate the DB on detected corruption — losing
historical reprint records but keeping the service alive. Adding a real
backup mechanism is one cron line + restore script, not a code change.

### TLS / mTLS to the printer
Cashino raw 9100 is plaintext — that's industry standard for in-cabinet
printer links. The RVM lives on an internal network and the printer
trust boundary is physical. If the deployment ever needs encryption, a
stunnel sidecar wraps the link without changing service code.

### Multi-tenant / multi-RVM in one instance
By design the service is single-tenant: one instance per RVM, with its
own `jobs.db` and `.env`. Scale-out is `N` copies, not `N` tenants in
one process. Choices like the in-memory paper predictor and the global
`asyncio.Lock` would all need rework for multi-tenancy — large refactor
for a problem the deployment doesn't have.

### Real USB end-to-end test suite
All 160 tests use the in-process mock transport (or the PTY-bridged
mock device in Tier 2 / TCP-bridged in Tier 3). Real USB (libusb +
Cashino over a physical cable) is verified manually on-site during the
third interview stage. Hardware-in-the-loop testing requires the
hardware; no clean way to CI it without a USB device pool.

### DLE ENQ recovery commands (resume-from-error-line)
ESC/POS offers `DLE ENQ n=1` to resume printing from the failing line
instead of from the beginning. Our recovery model is **reprint full** —
on any device fault we throw away the partial output and replay the
stored ESC/POS bytes. This costs paper but never produces a partial
receipt, which is the correct trade-off for financial records.

### Clock-skew tolerance for reprint TTL
The `REPRINT_MAX_AGE_HOURS` check uses wall-clock `datetime.now(UTC)`.
A DST jump or NTP correction shifts the cutoff by ≤1 hour twice a year.
Using monotonic clock here would mean tracking job ages across restarts —
overhead for a marginal payoff. Documented and accepted.

### Defense-in-depth items left as exercises
- Aspect-ratio padding for `/print/image` (currently squashes 10:1 inputs).
- USB hot-unplug detection beyond the ~1 s poll latency.
- Configurable `STATUS_READ_TIMEOUT_MS` separate from connect timeout.
- Per-job IDOR protection on `/reprint` (assumes trusted clients today).
