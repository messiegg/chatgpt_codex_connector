from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, status

from bridge_server.config import BridgeSettings
from bridge_server.schemas import CreateJobRequest, HealthResponse, JobResponse, ListJobsResponse
from storage import JobRecord, JobRepository, JobStatus


def _job_to_response(job: JobRecord) -> JobResponse:
    return JobResponse(**job.to_dict())


class JobService:
    def __init__(self, settings: BridgeSettings, repository: JobRepository) -> None:
        self.settings = settings
        self.repository = repository

    def initialize(self) -> None:
        self.settings.ensure_runtime_dirs()
        self.repository.database.initialize()

    def create_job(self, request: CreateJobRequest) -> JobResponse:
        job_id = str(uuid4())
        work_dir = Path(request.work_dir or self.settings.default_work_dir).expanduser().resolve()
        artifact_dir = (self.settings.artifacts_dir / job_id).resolve()

        work_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        job = self.repository.create_job(
            job_id=job_id,
            prompt=request.prompt,
            work_dir=work_dir,
            artifact_dir=artifact_dir,
        )
        return _job_to_response(job)

    def get_job(self, job_id: str) -> JobResponse:
        job = self.repository.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found",
            )
        return _job_to_response(job)

    def list_jobs(
        self,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
    ) -> ListJobsResponse:
        parsed_status = None
        if status_filter is not None:
            try:
                parsed_status = JobStatus(status_filter)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status '{status_filter}'",
                ) from exc

        jobs = self.repository.list_jobs(
            status=parsed_status,
            limit=limit,
            offset=offset,
        )
        return ListJobsResponse(
            jobs=[_job_to_response(job) for job in jobs],
            limit=limit,
            offset=offset,
            status=parsed_status.value if parsed_status else None,
        )

    def get_artifact_path(self, job_id: str, name: str) -> Path:
        job = self.repository.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job '{job_id}' not found",
            )

        artifact_root = Path(job.artifact_dir).expanduser().resolve()
        candidate = (artifact_root / name).resolve()

        if not candidate.is_relative_to(artifact_root):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Artifact not found",
            )

        if not candidate.is_file():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Artifact not found",
            )

        return candidate

    def read_text_artifact(self, job_id: str, name: str) -> str:
        artifact_path = self.get_artifact_path(job_id, name)
        try:
            return artifact_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to read artifact '{name}'",
            ) from exc

    def health(self) -> HealthResponse:
        try:
            self.repository.database.healthcheck()
        except Exception:
            return HealthResponse(status="degraded", database_ok=False)
        return HealthResponse(status="ok", database_ok=True)
