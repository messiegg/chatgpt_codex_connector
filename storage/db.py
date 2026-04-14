from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
        prompt TEXT NOT NULL,
        work_dir TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        return_code INTEGER,
        error_message TEXT,
        summary TEXT,
        artifact_dir TEXT NOT NULL,
        command TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs (status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_created_at
    ON jobs (created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_jobs_status_created_at
    ON jobs (status, created_at)
    """,
)


class SQLiteDatabase:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)

    def healthcheck(self) -> None:
        with self.connection() as connection:
            connection.execute("SELECT 1").fetchone()
