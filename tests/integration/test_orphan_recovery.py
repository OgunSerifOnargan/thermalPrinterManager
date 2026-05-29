"""G1: orphan job recovery — RECEIVED/PRINTING rows survived a crash."""
from __future__ import annotations

from datetime import UTC, datetime

from app.core.states import JobStatus
from app.services.job_repository import JobRecord, JobRepository


def _row(job_id: str, status: JobStatus) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        idempotency_key=None,
        op_type="print_text",
        status=status,
        error_code=None,
        error_detail=None,
        ts=datetime.now(UTC),
        completed_ts=None,
        duration_ms=None,
        payload_bytes=b"\x1b@payload",
    )


def test_reap_marks_printing_jobs_error(tmp_path):
    repo = JobRepository(db_path=str(tmp_path / "jobs.db"))
    repo.save(_row("alive-1", JobStatus.DONE))
    repo.save(_row("orphan-print", JobStatus.PRINTING))
    repo.save(_row("orphan-recv", JobStatus.RECEIVED))

    count = repo.reap_orphan_active_jobs(reason="unit test")
    assert count == 2

    done = repo.get("alive-1")
    assert done is not None and done.status == JobStatus.DONE
    for jid in ("orphan-print", "orphan-recv"):
        rec = repo.get(jid)
        assert rec is not None
        assert rec.status == JobStatus.ERROR
        assert rec.error_code == "COMM_ERROR"
        assert rec.error_detail == "unit test"
        assert rec.completed_ts is not None
    repo.close()


def test_reap_is_idempotent_zero_when_clean(tmp_path):
    repo = JobRepository(db_path=str(tmp_path / "jobs.db"))
    repo.save(_row("done", JobStatus.DONE))
    repo.save(_row("error", JobStatus.ERROR))
    assert repo.reap_orphan_active_jobs() == 0
    repo.close()
