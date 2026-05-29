# Test Summary

The test suite covers 160 cases across unit and integration layers,
using the in-process mock transport for deterministic causality. Real
hardware is verified manually on-site during the interview stage 3 —
see [`limitations.md`](limitations.md).

Run:

```bash
.venv/bin/pytest                          # all 160
.venv/bin/pytest tests/unit/              # 81 unit
.venv/bin/pytest tests/integration/       # 79 integration
.venv/bin/pytest -k fence                 # GS r 1 fence cases
.venv/bin/pytest tests/unit/test_renderer.py -v
```

CI-suitable: no external resources required.

---

## 1. Test inventory

### Unit (81 tests — `tests/unit/`)

| File | Tests | What it covers |
|---|---|---|
| `test_mock_printer.py` | 17 | Paper counter, head heat model, jam/cover bits, overheat hysteresis, online flag causality |
| `test_renderer.py` | 12 | Block composition, INIT + codepage, machine_id placement, reward styling, QR, cut, localized headers, truncation |
| `test_mock_transport.py` | 9 | DeviceTransport contract — connect, comm error, send, read_status, mode property |
| `test_prediction.py` | 8 | `PaperPredictor` — initial estimate, decreasing paper, image height, double-height accounting, QR fixed-block |
| `test_error_policy.py` | 8 | Every `ErrorCode` has a policy; HTTP status mapping; recovery semantics |
| `test_encoding.py` | 8 | cp857 native chars, ₺ → TL transliteration, smart-quote fallback, `USE_LIRA_SYMBOL` flag |
| `test_config.py` | 7 | Pydantic fail-fast: invalid port / mode rejected, overheat hysteresis check, log dir created |
| `test_log_scrub.py` | 5 | `[REDACTED]` for `qr_content`, `machine_id`, `items`, `total_reward`; safe keys preserved |
| `test_receipt_timezone.py` | 4 | `LOCAL_TIMEZONE` shifts UTC → Istanbul; naive datetime treated as UTC; invalid TZ rejected |
| `test_localization.py` | 3 | TR vs EN `_Localized` strings; explicit title overrides default |

### Integration (79 tests — `tests/integration/`)

| File | Tests | What it covers |
|---|---|---|
| `test_connection.py` | 12 | `/health`, `/connect`, `/disconnect`, cover/recovery polling, retry + breaker, mode change, cached `/status` non-blocking |
| `test_print_errors.py` | 12 | Happy path, paper decrement, all 6 error codes (PAPER_OUT / PAPER_JAM / COVER_OPEN / OVERHEAT auto-recover / COMM_ERROR / UNKNOWN_COMMAND), state transitions during print |
| `test_bonus.py` | 9 | Prediction in `/status`, ETA populates after print, `PATCH /config` happy path + token enforcement |
| `test_image_upload.py` | 6 | Image happy path, size cap (`MAX_IMAGE_BYTES`), invalid base64, non-image bytes, `/mock/preview` |
| `test_mock_endpoints.py` | 5 | `set_paper`, `set_cover`, `run_scenario` (overheat / recover_all), dev-mode gating |
| `test_logs.py` | 4 | `/logs` endpoint, level filter, CSV export, print appends an event |
| `test_print_done_fence.py` | 4 | `GS r 1` byte sent after payload, fence disabled path, fence timeout → COMM_ERROR, concurrent prints serialize through lock |
| `test_reprint.py` | 4 | Unknown job → 404, replay stored bytes byte-identical, reprint after failure, TTL expiry → 410 |
| `test_atomic_config.py` | 4 | Schema rejection short-circuits, all-valid commits, empty body 400, settings-level invalid is atomic |
| `test_deep_health.py` | 3 | `/healthz` green when idle, 503 if reconcile dead, healthy when connected |
| `test_idempotency.py` | 3 | Same key dedupes, different keys distinct, no key does not dedupe |
| `test_rate_limit.py` | 3 | 429 after limit, zero disables, per-IP via `X-Forwarded-For` |
| `test_job_retention.py` | 3 | Sliding-window purge, no-op when nothing old, many large rows handled |
| `test_orphan_recovery.py` | 2 | Restart sweep marks PRINTING → ERROR; idempotent on clean DB |
| `test_receipt_size_limit.py` | 2 | Oversized payload returns 400, normal payload passes |
| `test_unhandled_exception.py` | 2 | Generic exception → structured 500, HTTPException passes through |
| `test_init_on_connect.py` | 1 | First 2 bytes after `/connect` are `ESC @` (1B 40) |

---

## 2. Requirement coverage matrix

Mapping `Question/requirement.md` items → tests / code that prove them.

| Requirement | Test(s) / verification | Code location |
|---|---|---|
| **Mandatory** | | |
| 1.1 USB + Ethernet | `test_mock_transport.py` (contract) + manual real-device | `app/devices/{serial,real,usb}_transport.py` |
| 1.1 UI shows active mode | manual demo + `test_connection.py::test_connect_then_status_shows_idle` | `app/ui/app.js` connection panel |
| 1.1 Auto-reconnect + backoff | `test_connection.py::test_reconcile_retries_after_connect_failure`, `::test_breaker_opens_after_threshold_failures` | `connection_manager.py` + `reconcile_loop.py` |
| 1.2 POST `/connect` | `test_connection.py::test_connect_then_status_shows_idle` | `app/api/routes.py:/connect` |
| 1.2 POST `/print/text` | `test_print_errors.py::test_print_text_happy_path` + 11 others | `routes.py:/print/text` |
| 1.2 POST `/print/image` | `test_image_upload.py::test_print_image_happy_path` + 5 others | `routes.py:/print/image` |
| 1.2 GET `/status` | `test_connection.py::test_status_reflects_cover_open_after_poll`, `::test_cached_status_does_not_block_on_device` | `routes.py:/status` |
| 1.2 GET `/logs` | `test_logs.py::test_logs_endpoint_returns_entries` (+3 others) | `routes.py:/logs` |
| 1.2 POST `/reprint` | `test_reprint.py` (4 tests) | `routes.py:/reprint` |
| 1.3 6 error codes (PAPER_OUT…UNKNOWN_COMMAND) | `test_error_policy.py` (8 tests) + `test_print_errors.py` (every code) | `app/core/errors.py:ERROR_POLICY` |
| 1.3 Failed images reprintable | `test_reprint.py::test_reprint_after_failure` | `job_repository.py` BLOB column |
| 1.4 JSON log schema + scrub | `test_log_scrub.py` (5 tests), `test_logs.py::test_print_appends_to_logs` | `app/core/logging.py` `JsonlFormatter` |
| **Bonus** | | |
| UI dashboard | manual demo | `app/ui/` |
| Prediction (paper / ETA) | `test_prediction.py` (8), `test_bonus.py::test_eta_text_populated_after_print` | `prediction.py`, `eta.py` |
| Queue / idempotency | `test_idempotency.py` (3) | `idempotency_key UNIQUE` + `asyncio.Lock` |
| Retry/backoff + `/health` | `test_connection.py` + `test_deep_health.py` | `connection_manager.py`, `routes.py:/healthz` |
| Dockerfile / compose | manual `docker compose up` + healthcheck | `Dockerfile`, `Dockerfile.mock`, `docker-compose.yml` |
| CSV log export | `test_logs.py::test_logs_export_csv` | `routes.py:/logs/export` |
| Basic auth (token) | `test_bonus.py::test_patch_config_token_enforced` | `app/core/auth.py` |
| Turkish + English | `test_localization.py` (3), `test_renderer.py::test_table_header_localized_{tr,en}` | `_Localized` dataclass in `receipt_renderer.py` |
| Text / image / QR | renderer tests + `test_image_upload.py` | `receipt_renderer.py`, `image_processor.py` |

---

## 3. Test architecture

```
┌──────────────────────────────────────────────────────┐
│ Tier 1: In-process unit + integration                │
│   FastAPI TestClient → routes → service → mock brain │
│   No sockets, no PTY. Fastest, fully deterministic.  │
│   (160/160 tests live here)                          │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│ Tier 2: Standalone mock over TCP / PTY               │
│   scripts/mock_device_server.py +                    │
│   TRANSPORT_BACKEND=real → real LanTransport/Serial  │
│   Manual / smoke. Validates byte fidelity over wire. │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│ Tier 3: Real Cashino device                          │
│   On-site, interview stage 3. Manual smoke + visual  │
│   check (Turkish glyphs, ₺ rendering, cut quality).  │
└──────────────────────────────────────────────────────┘
```

The same `MockPrinter` brain backs Tier 1 (direct in-process) and Tier 2
(behind a TCP server / PTY bridge), so a passing Tier 1 test gives high
confidence that Tier 2 will pass byte-for-byte modulo the parser bug
class (which the ESC/POS-aware `_EscPosScanner` now precludes — see
[`escpos.md §5`](escpos.md#5-the-paper_jam-case-study)).

---

## 4. Fixtures and utilities

* `tests/conftest.py` — top-level fixtures: `settings`, `clock`,
  `client` (httpx ASGITransport), `mock_brain`, `connected_service`.
* `tests/_helpers/` — assertion helpers (status decode, JSONL probing).
* `FakeClock` — deterministic time control for heat / cool tests.

---

## 5. What we deliberately do not test in CI

* **Real USB enumeration.** Requires hardware. Manual on-site.
* **Glyph fidelity on real Cashino firmware.** Visual inspection only.
* **PTY USB-CDC bridge end-to-end.** Works on host (Tier 2), not in CI
  containers — kernel limitation, not a code issue.
* **Multi-process or multi-instance behavior.** Out of scope —
  single-tenant by design.

---

## 6. Maintenance

Adding a new test:

1. Decide unit vs integration based on whether it crosses the
   FastAPI/service boundary.
2. Pick an existing file by topic or create one (rule of thumb: > 5
   tests on a topic → its own file).
3. If a new error code is added: extend `ERROR_POLICY` first, then add
   a unit test in `test_error_policy.py` and an integration test in
   `test_print_errors.py`.
4. Update this file's tables.

---

## Cross-references

* Endpoint-by-endpoint behaviour: [`api.md`](api.md)
* Error code semantics: [`errors.md`](errors.md)
* The ESC/POS protocol context tests rely on: [`escpos.md`](escpos.md)
