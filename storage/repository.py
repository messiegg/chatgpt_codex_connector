from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .db import SQLiteDatabase
from .models import JobRecord, JobStatus


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_job(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        job_id=row["job_id"],
        status=JobStatus(row["status"]),
        prompt=row["prompt"],
        work_dir=row["work_dir"],
        created_at=row["created_at"],
        artifact_dir=row["artifact_dir"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        return_code=row["return_code"],
        error_message=row["error_message"],
        summary=row["summary"],
        command=row["command"],
    )


class JobRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create_job(
        self,
        *,
        prompt: str,
        work_dir: str | Path,
        artifact_dir: str | Path,
        command: str | None = None,
        job_id: str | None = None,
    ) -> JobRecord:
        created_at = utc_now_iso()
        resolved_job_id = job_id or str(uuid4())
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id,
                    status,
                    prompt,
                    work_dir,
                    created_at,
                    artifact_dir,
                    command
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_job_id,
                    JobStatus.QUEUED.value,
                    prompt,
                    str(Path(work_dir).expanduser().resolve()),
                    created_at,
                    str(Path(artifact_dir).expanduser().resolve()),
                    command,
                ),
            )
        return self.get_job(resolved_job_id)  # type: ignore[return-value]

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def list_jobs(
        self,
        *,
        status: JobStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobRecord]:
        sql = "SELECT * FROM jobs"
        params: list[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.database.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_job(row) for row in rows]

    def list_all_jobs(self, *, status: JobStatus | None = None) -> list[JobRecord]:
        sql = "SELECT * FROM jobs"
        params: list[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value)
        with self.database.connection() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_row_to_job(row) for row in rows]

    def claim_next_queued_job(self) -> JobRecord | None:
        started_at = utc_now_iso()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (JobStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None

            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, started_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    JobStatus.RUNNING.value,
                    started_at,
                    row["job_id"],
                    JobStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None

            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
            connection.commit()

        if claimed is None:
            return None
        return _row_to_job(claimed)

    def update_job_result(
        self,
        job_id: str,
        *,
        status: JobStatus,
        return_code: int | None,
        error_message: str | None,
        summary: str | None,
        command: str | None = None,
    ) -> JobRecord | None:
        if status not in (JobStatus.SUCCEEDED, JobStatus.FAILED):
            raise ValueError("status must be succeeded or failed")

        finished_at = utc_now_iso()
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET
                    status = ?,
                    finished_at = ?,
                    return_code = ?,
                    error_message = ?,
                    summary = ?,
                    command = COALESCE(?, command)
                WHERE job_id = ?
                """,
                (
                    status.value,
                    finished_at,
                    return_code,
                    error_message,
                    summary,
                    command,
                    job_id,
                ),
            )
        return self.get_job(job_id)
