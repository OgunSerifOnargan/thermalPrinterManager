"""SQLite job store (per E4, BLOB-based as user confirmed).

Schema is created at startup. `idempotency_key UNIQUE` (per R5) protects
against double-submission races. `payload_bytes` BLOB holds the rendered
ESC/POS stream so reprint produces a byte-for-byte identical receipt
even if the renderer changes later.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.states import JobStatus


log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    idempotency_key  TEXT UNIQUE,
    op_type          TEXT NOT NULL,
    status           TEXT NOT NULL,
    error_code       TEXT,
    error_detail     TEXT,
    ts               TEXT NOT NULL,
    completed_ts     TEXT,
    duration_ms      INTEGER,
    payload_bytes    BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_ts ON jobs(ts);
"""


@dataclass
class JobRecord:
    job_id: str
    idempotency_key: str | None
    op_type: str
    status: JobStatus
    error_code: str | None
    error_detail: str | None
    ts: datetime
    completed_ts: datetime | None
    duration_ms: int | None
    payload_bytes: bytes

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> JobRecord:
        return cls(
            job_id=row["job_id"],
            idempotency_key=row["idempotency_key"],
            op_type=row["op_type"],
            status=JobStatus(row["status"]),
            error_code=row["error_code"],
            error_detail=row["error_detail"],
            ts=datetime.fromisoformat(row["ts"]),
            completed_ts=(
                datetime.fromisoformat(row["completed_ts"])
                if row["completed_ts"] else None
            ),
            duration_ms=row["duration_ms"],
            payload_bytes=bytes(row["payload_bytes"]),
        )


class JobRepository:
    """Synchronous SQLite repository.

    Operations are single-row and fast; safe to call from async code as long
    as the caller serializes with the appropriate lock (the PrinterService
    uses the connection lock around its workflow).
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        # `check_same_thread=False` lets us share the connection across
        # the asyncio thread and any test threads; we serialize with our
        # own lock since SQLite itself locks the file.
        self._conn = sqlite3.connect(
            db_path,
            isolation_level=None,    # autocommit; we use explicit transactions
            check_same_thread=False,
            timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("PRAGMA journal_mode=WAL;")
        self._lock = threading.Lock()
        self._bootstrap()

    def _bootstrap(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- Writes -----

    def save(self, record: JobRecord) -> tuple[JobRecord, bool]:
        """Insert a new job. Returns (record_to_use, was_newly_inserted).

        If `record.idempotency_key` is set and already exists, the existing
        row is returned (race-safe via UNIQUE constraint).
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO jobs"
                " (job_id, idempotency_key, op_type, status, error_code, error_detail,"
                "  ts, completed_ts, duration_ms, payload_bytes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.job_id,
                    record.idempotency_key,
                    record.op_type,
                    record.status.value,
                    record.error_code,
                    record.error_detail,
                    record.ts.isoformat(),
                    record.completed_ts.isoformat() if record.completed_ts else None,
                    record.duration_ms,
                    record.payload_bytes,
                ),
            )
            inserted = cur.rowcount == 1

        if not inserted and record.idempotency_key:
            existing = self.get_by_idempotency_key(record.idempotency_key)
            if existing and existing.job_id != record.job_id:
                return existing, False
        return record, True

    def update(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        completed_ts: datetime | None = None,
        duration_ms: int | None = None,
    ) -> None:
        fields = []
        values: list = []
        if status is not None:
            fields.append("status = ?")
            values.append(status.value)
        if error_code is not None:
            fields.append("error_code = ?")
            values.append(error_code)
        if error_detail is not None:
            fields.append("error_detail = ?")
            values.append(error_detail)
        if completed_ts is not None:
            fields.append("completed_ts = ?")
            values.append(completed_ts.isoformat())
        if duration_ms is not None:
            fields.append("duration_ms = ?")
            values.append(duration_ms)
        if not fields:
            return
        values.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?"
        with self._lock:
            self._conn.execute(sql, values)

    # ----- Reads -----

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return JobRecord.from_row(row) if row else None

    def get_by_idempotency_key(self, key: str) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return JobRecord.from_row(row) if row else None

    def purge_older_than(self, days: int) -> int:
        """Sliding-window retention: hard-delete every job whose ts is older
        than `days`. Keeps jobs.db from growing without bound — image BLOBs
        run 50-300 KB each, so a busy RVM blows past 1 GB in months without
        this. Run at startup + hourly via the housekeeping task.
        """
        from datetime import UTC, datetime, timedelta
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM jobs WHERE ts < ?", (cutoff,),
            )
            deleted = cur.rowcount or 0
            if deleted:
                # Reclaim BLOB space immediately; small DBs only.
                self._conn.execute("VACUUM")
            return deleted

    def reap_orphan_active_jobs(self, reason: str = "service restart") -> int:
        """G1: at startup, sweep jobs left in RECEIVED/PRINTING by a previous crash.

        Returns the number of rows reaped. Each becomes status=ERROR with a
        COMM_ERROR code so reprint logic, UI status, and ETA queries stay
        consistent. Without this, killed-while-printing rows linger forever
        and the UI reads `last_job.status="printing"` indefinitely.
        """
        from datetime import UTC, datetime
        now_iso = datetime.now(UTC).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs"
                "  SET status = ?, error_code = ?, error_detail = ?, completed_ts = ?"
                " WHERE status IN (?, ?)",
                (
                    JobStatus.ERROR.value,
                    "COMM_ERROR",
                    reason,
                    now_iso,
                    JobStatus.RECEIVED.value,
                    JobStatus.PRINTING.value,
                ),
            )
            return cur.rowcount or 0

    def list_failed(self, limit: int = 50,
                    max_age_hours: int | None = None) -> list[JobRecord]:
        params: list = [JobStatus.ERROR.value]
        sql = "SELECT * FROM jobs WHERE status = ?"
        if max_age_hours:
            cutoff = (datetime.now(UTC) - timedelta(hours=max_age_hours)).isoformat()
            sql += " AND ts >= ?"
            params.append(cutoff)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [JobRecord.from_row(r) for r in rows]

    def get_latest(self) -> JobRecord | None:
        """Most recently created job — used by /mock/preview."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return JobRecord.from_row(row) if row else None

    def get_last_failed_within_ttl(self, max_age_hours: int) -> JobRecord | None:
        """Most recent ERROR-status job that is still inside the reprint
        TTL window. Powers `POST /reprint/last-failed` — the caller
        gets the latest unfinished job back without needing to know its
        UUID. Returns None when no such job exists (queue empty, all
        jobs done, or the failed ones aged out)."""
        failed = self.list_failed(limit=1, max_age_hours=max_age_hours)
        return failed[0] if failed else None

    def list_recent_done(self, op_type: str, limit: int = 10) -> list[JobRecord]:
        """For ETA — recent successful prints of a given op_type, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? AND op_type = ? AND duration_ms IS NOT NULL"
                " ORDER BY completed_ts DESC LIMIT ?",
                (JobStatus.DONE.value, op_type, limit),
            ).fetchall()
        return [JobRecord.from_row(r) for r in rows]
