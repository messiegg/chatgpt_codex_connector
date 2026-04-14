from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(slots=True)
class JobRecord:
    job_id: str
    status: JobStatus
    prompt: str
    work_dir: str
    created_at: str
    artifact_dir: str
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    error_message: str | None = None
    summary: str | None = None
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload
