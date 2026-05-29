"""Integration: /reprint — happy, not-found, expired."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from app.devices.mock_printer import MockPrinter
from app.services.job_repository import JobRepository


def base_body() -> dict:
    return {
        "machine_id": "ACO-RP",
        "items": [{"product": "Plastic", "quantity": 2, "reward": 2.0}],
        "total_reward": 2.0,
        "timestamp": "2026-05-27T22:00:00Z",
        "lang": "tr",
        "title": "Reprint test",
    }


async def test_reprint_unknown_job_id_returns_404(client: AsyncClient):
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/reprint", json={"job_id": "does-not-exist"})
    assert r.status_code == 404
    assert r.json()["error_code"] == "NOT_FOUND"


async def test_reprint_done_job_returns_already_done_without_printing(
    client: AsyncClient, mock_printer: MockPrinter,
    repository: JobRepository,
):
    """A reprint call with a UUID whose original job already succeeded
    must NOT produce a duplicate receipt. The /reprint contract is
    "recover a failed job"; a DONE uuid means the customer (probably
    accidentally) asked for the wrong thing.

    Response: 200 with status="already_done", job_id is the *original*
    uuid (no new job created), duration_ms reflects the original print.
    Paper count must not change — no bytes were sent."""
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json=base_body())
    assert r.status_code == 200
    done_id = r.json()["job_id"]
    paper_after_first_print = mock_printer.paper_lines

    r2 = await client.post("/reprint", json={"job_id": done_id})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["ok"] is True
    assert body["status"] == "already_done"
    assert body["job_id"] == done_id              # same id, no new job
    assert body["duration_ms"] is not None         # original duration_ms surfaced
    # No bytes left the service — paper count stayed put.
    assert mock_printer.paper_lines == paper_after_first_print


async def test_reprint_failed_job_replays_bytes(
    client: AsyncClient, mock_printer: MockPrinter,
    repository: JobRepository,
):
    """The byte-identical replay property holds for FAILED jobs — which
    is the actual use case for /reprint. Causes a paper-out, reprints
    after recovery, and checks the stored ESC/POS payload was sent
    again (paper decremented)."""
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    r = await client.post("/print/text", json=base_body())
    assert r.status_code == 503
    failed_id = repository.list_failed(limit=1)[0].job_id
    mock_printer.set_cover(False)

    paper_before = mock_printer.paper_lines
    r2 = await client.post("/reprint", json={"job_id": failed_id})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["ok"] is True
    assert body["status"] == "done"
    assert body["job_id"] != failed_id    # reprint creates a new job
    # Same payload bytes were sent — paper dropped.
    assert mock_printer.paper_lines < paper_before


async def test_reprint_after_failure(client: AsyncClient,
                                     mock_printer: MockPrinter,
                                     repository: JobRepository):
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    r = await client.post("/print/text", json=base_body())
    assert r.status_code == 503
    failed = repository.list_failed(limit=5)
    assert len(failed) >= 1
    failed_id = failed[0].job_id
    # User closes cover, reprints
    mock_printer.set_cover(False)
    r2 = await client.post("/reprint", json={"job_id": failed_id})
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"


async def test_reprint_expired_returns_410(client: AsyncClient,
                                           mock_printer: MockPrinter,
                                           repository: JobRepository,
                                           settings):
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json=base_body())
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    # Backdate the job past TTL
    too_old = (datetime.now(UTC) - timedelta(
        hours=settings.reprint_max_age_hours + 1
    )).isoformat()
    # Directly edit the row (test backdoor)
    with repository._lock:
        repository._conn.execute(
            "UPDATE jobs SET ts = ? WHERE job_id = ?", (too_old, job_id)
        )
    r2 = await client.post("/reprint", json={"job_id": job_id})
    assert r2.status_code == 410
    assert r2.json()["error_code"] == "GONE"


# ----- /reprint/last-failed -----


async def test_last_failed_with_no_jobs_returns_404(client: AsyncClient):
    """Empty job store → NO_FAILED_JOB (a distinct error code from
    /reprint's NOT_FOUND so the UI can tell "you gave a bad id" from
    "there is nothing to reprint" apart)."""
    r = await client.post("/reprint/last-failed")
    assert r.status_code == 404
    assert r.json()["error_code"] == "NO_FAILED_JOB"


async def test_last_failed_when_only_successful_jobs_returns_404(
    client: AsyncClient, mock_printer: MockPrinter,
):
    """A clean queue (every job DONE) still returns NO_FAILED_JOB —
    "last failed" filters on status=ERROR, not just "most recent"."""
    await client.post("/connect", json={"mode": "lan"})
    r = await client.post("/print/text", json=base_body())
    assert r.status_code == 200
    r2 = await client.post("/reprint/last-failed")
    assert r2.status_code == 404
    assert r2.json()["error_code"] == "NO_FAILED_JOB"


async def test_last_failed_picks_most_recent_error_and_reprints(
    client: AsyncClient, mock_printer: MockPrinter,
    repository: JobRepository,
):
    """Happy path. Two failures with a successful print in between —
    the endpoint picks the *most recent* ERROR-status job (not the
    successful one between them) and reprints it."""
    await client.post("/connect", json={"mode": "lan"})
    # Failure #1
    mock_printer.set_cover(True)
    r1 = await client.post("/print/text", json=base_body())
    assert r1.status_code == 503
    # Successful in between
    mock_printer.set_cover(False)
    r2 = await client.post("/print/text", json=base_body())
    assert r2.status_code == 200
    # Failure #2 (this is the one /reprint/last-failed should pick)
    mock_printer.set_cover(True)
    r3 = await client.post("/print/text", json=base_body())
    assert r3.status_code == 503
    failure2_id = repository.list_failed(limit=5)[0].job_id

    # Clear the fault, fire /reprint/last-failed — no body needed
    mock_printer.set_cover(False)
    r4 = await client.post("/reprint/last-failed")
    assert r4.status_code == 200, r4.text
    body = r4.json()
    assert body["ok"] is True
    assert body["status"] == "done"
    assert body["job_id"] != failure2_id   # reprint creates a new job


async def test_last_failed_outside_ttl_returns_404(
    client: AsyncClient, mock_printer: MockPrinter,
    repository: JobRepository, settings,
):
    """A failed job that's past REPRINT_MAX_AGE_HOURS is treated as
    "no failed job" — same TTL semantics as the regular /reprint, so
    one stale row doesn't keep haunting the endpoint forever."""
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    r = await client.post("/print/text", json=base_body())
    assert r.status_code == 503
    failed_id = repository.list_failed(limit=5)[0].job_id

    too_old = (datetime.now(UTC) - timedelta(
        hours=settings.reprint_max_age_hours + 1
    )).isoformat()
    with repository._lock:
        repository._conn.execute(
            "UPDATE jobs SET ts = ? WHERE job_id = ?", (too_old, failed_id)
        )

    r2 = await client.post("/reprint/last-failed")
    assert r2.status_code == 404
    assert r2.json()["error_code"] == "NO_FAILED_JOB"


# ----- /jobs/failed (recent failure picker) -----


async def test_jobs_failed_empty_returns_empty_list(client: AsyncClient):
    """No failures yet → 200 with an empty jobs array, NOT 404. The
    UI calls this on every page load and wants to render an empty
    dropdown, not handle an error."""
    r = await client.get("/jobs/failed")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["jobs"] == []
    assert body["limit"] == 5            # default


async def test_jobs_failed_returns_newest_first_capped_by_limit(
    client: AsyncClient, mock_printer: MockPrinter,
    repository: JobRepository,
):
    """Three failures, ask for limit=2 → only the newest two. Order
    must be most-recent-first so the dropdown's top entry is what the
    user just saw fail."""
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    ids: list[str] = []
    for _ in range(3):
        r = await client.post("/print/text", json=base_body())
        assert r.status_code == 503
        ids.append(repository.list_failed(limit=1)[0].job_id)

    r = await client.get("/jobs/failed?limit=2")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    # list_failed orders by ts DESC; ids[] was built oldest→newest.
    returned = [j["job_id"] for j in body["jobs"]]
    assert returned == [ids[-1], ids[-2]]
    # Each entry carries the fields the picker needs.
    for j in body["jobs"]:
        assert {"job_id", "op_type", "error_code", "ts"}.issubset(j)
        # PII not exposed (machine_id, items, payload bytes).
        assert "machine_id" not in j
        assert "payload_bytes" not in j


async def test_jobs_failed_drops_entries_outside_ttl(
    client: AsyncClient, mock_printer: MockPrinter,
    repository: JobRepository, settings,
):
    """A failure backdated past REPRINT_MAX_AGE_HOURS doesn't appear
    in the picker — same TTL contract as /reprint and
    /reprint/last-failed."""
    await client.post("/connect", json={"mode": "lan"})
    mock_printer.set_cover(True)
    r = await client.post("/print/text", json=base_body())
    assert r.status_code == 503
    failed_id = repository.list_failed(limit=1)[0].job_id

    too_old = (datetime.now(UTC) - timedelta(
        hours=settings.reprint_max_age_hours + 1
    )).isoformat()
    with repository._lock:
        repository._conn.execute(
            "UPDATE jobs SET ts = ? WHERE job_id = ?", (too_old, failed_id)
        )

    r2 = await client.get("/jobs/failed")
    assert r2.status_code == 200
    assert r2.json()["count"] == 0


async def test_jobs_failed_validates_limit_range(client: AsyncClient):
    """limit must be 1..50 (Pydantic Query validation). 0 / negative /
    huge values are rejected so a misconfigured client can't dump the
    whole DB through this endpoint."""
    r = await client.get("/jobs/failed?limit=0")
    assert r.status_code == 422
    r = await client.get("/jobs/failed?limit=100")
    assert r.status_code == 422
