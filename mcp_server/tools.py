from __future__ import annotations

import logging
from time import monotonic, sleep
from typing import Final

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel

from bridge_server.schemas import CreateJobRequest, JobResponse, ListJobsResponse
from bridge_server.service import JobService


logger = logging.getLogger(__name__)

ALLOWED_TEXT_ARTIFACTS: Final[frozenset[str]] = frozenset(
    {
        "summary.txt",
        "stdout.log",
        "stderr.log",
        "prompt.txt",
        "metadata.json",
    }
)
MAX_ARTIFACT_CONTENT_CHARS: Final[int] = 50_000
MAX_LIST_LIMIT: Final[int] = 100
TERMINAL_JOB_STATUSES: Final[frozenset[str]] = frozenset({"succeeded", "failed"})


class ArtifactContentResponse(BaseModel):
    job_id: str
    name: str
    content: str
    truncated: bool


def _raise_tool_error(message: str) -> None:
    raise ToolError(message)


def _handle_http_exception(exc: HTTPException) -> None:
    detail = exc.detail
    if isinstance(detail, str) and detail:
        _raise_tool_error(detail)
    _raise_tool_error("The request could not be completed")


def _require_non_empty(value: str, field_name: str) -> str:
    if not value:
        _raise_tool_error(f"{field_name} must not be empty")
    return value


def register_tools(mcp: FastMCP, service: JobService) -> None:
    @mcp.tool(
        name="create_job",
        description="Create a bridge job for the local Codex worker.",
        structured_output=True,
    )
    def create_job(prompt: str, work_dir: str | None = None) -> JobResponse:
        normalized_prompt = _require_non_empty(prompt, "prompt")
        try:
            payload = CreateJobRequest(prompt=normalized_prompt, work_dir=work_dir)
            return service.create_job(payload)
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("create_job tool failed")
            _raise_tool_error("Failed to create job")

    @mcp.tool(
        name="get_job",
        description="Get the current state and result for a single job.",
        structured_output=True,
    )
    def get_job(job_id: str) -> JobResponse:
        normalized_job_id = _require_non_empty(job_id, "job_id")
        try:
            return service.get_job(normalized_job_id)
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("get_job tool failed")
            _raise_tool_error("Failed to load job")

    @mcp.tool(
        name="list_jobs",
        description="List recent jobs, optionally filtered by status.",
        structured_output=True,
    )
    def list_jobs(
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ListJobsResponse:
        if limit < 1 or limit > MAX_LIST_LIMIT:
            _raise_tool_error(f"limit must be between 1 and {MAX_LIST_LIMIT}")
        if offset < 0:
            _raise_tool_error("offset must be greater than or equal to 0")

        try:
            return service.list_jobs(status_filter=status, limit=limit, offset=offset)
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("list_jobs tool failed")
            _raise_tool_error("Failed to list jobs")

    @mcp.tool(
        name="get_artifact",
        description="Read a supported text artifact for a completed or running job.",
        structured_output=True,
    )
    def get_artifact(job_id: str, name: str) -> ArtifactContentResponse:
        normalized_job_id = _require_non_empty(job_id, "job_id")
        normalized_name = _require_non_empty(name, "name")

        if normalized_name not in ALLOWED_TEXT_ARTIFACTS:
            allowed = ", ".join(sorted(ALLOWED_TEXT_ARTIFACTS))
            _raise_tool_error(
                f"Artifact '{normalized_name}' is not supported. Allowed artifacts: {allowed}"
            )

        try:
            content = service.read_text_artifact(normalized_job_id, normalized_name)
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("get_artifact tool failed")
            _raise_tool_error("Failed to read artifact")

        truncated = False
        if len(content) > MAX_ARTIFACT_CONTENT_CHARS:
            content = content[:MAX_ARTIFACT_CONTENT_CHARS]
            truncated = True

        return ArtifactContentResponse(
            job_id=normalized_job_id,
            name=normalized_name,
            content=content,
            truncated=truncated,
        )

    @mcp.tool(
        name="wait_for_job",
        description="Poll a job until it reaches a terminal state or times out.",
        structured_output=True,
    )
    def wait_for_job(
        job_id: str,
        timeout_seconds: int = 120,
        poll_interval: float = 2.0,
    ) -> JobResponse:
        normalized_job_id = _require_non_empty(job_id, "job_id")
        if timeout_seconds < 1:
            _raise_tool_error("timeout_seconds must be greater than or equal to 1")
        if poll_interval <= 0:
            _raise_tool_error("poll_interval must be greater than 0")

        deadline = monotonic() + timeout_seconds

        try:
            while True:
                job = service.get_job(normalized_job_id)
                if job.status in TERMINAL_JOB_STATUSES:
                    return job

                if monotonic() >= deadline:
                    _raise_tool_error(
                        f"Timed out after {timeout_seconds} seconds waiting for job '{normalized_job_id}'"
                    )

                sleep(poll_interval)
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("wait_for_job tool failed")
            _raise_tool_error("Failed while waiting for job")
