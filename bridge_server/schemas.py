from __future__ import annotations

from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    prompt: str = Field(min_length=1)
    work_dir: str | None = None


class JobResponse(BaseModel):
    job_id: str
    status: str
    prompt: str
    work_dir: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    error_message: str | None = None
    summary: str | None = None
    artifact_dir: str
    command: str | None = None


class ListJobsResponse(BaseModel):
    jobs: list[JobResponse]
    limit: int
    offset: int
    status: str | None = None


class HealthResponse(BaseModel):
    status: str
    database_ok: bool
