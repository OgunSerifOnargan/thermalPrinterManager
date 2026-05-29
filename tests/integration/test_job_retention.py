"""Sliding-window job retention — purge_older_than wipes old rows + BLOBs."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.states import JobStatus
from app.services.job_repository import JobRecord, JobRepository


def _row(job_id: str, age_days: float) -> JobRecord:
    ts = datetime.now(UTC) - timedelta(days=age_days)
    return JobRecord(
        job_id=job_id,
        idempotency_key=None,
        op_type="print_text",
        status=JobStatus.DONE,
        error_code=None,
        error_detail=None,
        ts=ts,
        completed_ts=ts,
        duration_ms=10,
        payload_bytes=b"\x1b@" + b"x" * 50_000,    # bulk to confirm VACUUM helps
    )


def test_purge_drops_only_old_jobs(tmp_path):
    repo = JobRepository(db_path=str(tmp_path / "jobs.db"))
    repo.save(_row("fresh", age_days=0))
    repo.save(_row("middle", age_days=3))
    repo.save(_row("ancient-a", age_days=8))
    repo.save(_row("ancient-b", age_days=30))

    deleted = repo.purge_older_than(days=7)
    assert deleted == 2

    assert repo.get("fresh") is not None
    assert repo.get("middle") is not None
    assert repo.get("ancient-a") is None
    assert repo.get("ancient-b") is None
    repo.close()


def test_purge_zero_when_nothing_to_delete(tmp_path):
    repo = JobRepository(db_path=str(tmp_path / "jobs.db"))
    repo.save(_row("fresh-1", age_days=1))
    repo.save(_row("fresh-2", age_days=2))
    assert repo.purge_older_than(days=7) == 0
    repo.close()


def test_purge_handles_many_large_rows(tmp_path):
    """Purging a batch of BLOB-heavy rows should not raise and the rows go away."""
    repo = JobRepository(db_path=str(tmp_path / "jobs.db"))
    for i in range(10):
        repo.save(_row(f"old-{i}", age_days=10))
    assert repo.purge_older_than(days=7) == 10
    # All gone — list_failed() shouldn't see them either.
    assert repo.list_failed(limit=100) == []
    repo.close()
