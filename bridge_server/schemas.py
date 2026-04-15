from __future__ import annotations

from typing import Any

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


class AggregatedJobResult(BaseModel):
    job_id: str
    status: str
    summary: str
    stdout_tail: str
    stderr_tail: str
    work_dir: str
    artifact_dir: str
    artifact_names: list[str]
    return_code: int | None = None
    command: str | None = None
    duration_seconds: float | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    timed_out: bool | None = None
    metadata: dict[str, Any] | None = None


class RunCodexTaskResponse(AggregatedJobResult):
    timed_out: bool


class GetResultResponse(AggregatedJobResult):
    result_file_present: bool


class GetLatestResultResponse(GetResultResponse):
    resolved_job_id: str
