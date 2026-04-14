from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse

from bridge_server.config import BridgeSettings
from bridge_server.schemas import CreateJobRequest, HealthResponse, JobResponse, ListJobsResponse
from bridge_server.service import JobService
from storage import JobRepository, SQLiteDatabase


def build_service(settings: BridgeSettings | None = None) -> JobService:
    resolved_settings = settings or BridgeSettings.from_env()
    database = SQLiteDatabase(resolved_settings.database_path)
    repository = JobRepository(database)
    return JobService(resolved_settings, repository)


def create_app(settings: BridgeSettings | None = None) -> FastAPI:
    service = build_service(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service.initialize()
        app.state.job_service = service
        app.state.settings = service.settings
        yield

    app = FastAPI(title="Codex Bridge Demo", version="0.1.0", lifespan=lifespan)

    def get_service(request: Request) -> JobService:
        return request.app.state.job_service

    @app.get("/health", response_model=HealthResponse)
    def health(service: JobService = Depends(get_service)) -> HealthResponse:
        return service.health()

    @app.post("/jobs", response_model=JobResponse, status_code=201)
    def create_job(
        payload: CreateJobRequest,
        service: JobService = Depends(get_service),
    ) -> JobResponse:
        return service.create_job(payload)

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str, service: JobService = Depends(get_service)) -> JobResponse:
        return service.get_job(job_id)

    @app.get("/jobs", response_model=ListJobsResponse)
    def list_jobs(
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        service: JobService = Depends(get_service),
    ) -> ListJobsResponse:
        return service.list_jobs(status_filter=status, limit=limit, offset=offset)

    @app.get("/jobs/{job_id}/artifacts/{name}")
    def get_artifact(
        job_id: str,
        name: str,
        service: JobService = Depends(get_service),
    ) -> FileResponse:
        artifact_path = service.get_artifact_path(job_id, name)
        return FileResponse(path=artifact_path, filename=artifact_path.name)

    return app


app = create_app()
