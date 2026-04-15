from __future__ import annotations

import logging
from time import monotonic, sleep
from typing import Final

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel

from bridge_server.results import (
    build_aggregated_job_result,
    load_or_aggregate_result,
)
from bridge_server.schemas import (
    CreateJobRequest,
    GetLatestResultResponse,
    GetResultResponse,
    JobResponse,
    ListJobsResponse,
    ResultWidgetPayload,
    RunCodexTaskResponse,
)
from bridge_server.service import JobService
from mcp_server.result_widget import (
    RESULT_WIDGET_TOOL_META,
    build_data_result_tool_response,
    build_render_result_widget_response,
)


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
    if not value or not value.strip():
        _raise_tool_error(f"{field_name} must not be empty")
    return value.strip()


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

    @mcp.tool(
        name="run_codex_task",
        description="Create a job, wait for completion or timeout, and return aggregated execution results.",
        structured_output=True,
    )
    def run_codex_task(
        prompt: str,
        work_dir: str | None = None,
        timeout_seconds: int = 120,
        poll_interval: float = 2.0,
    ) -> RunCodexTaskResponse:
        normalized_prompt = _require_non_empty(prompt, "prompt")
        if timeout_seconds < 1:
            _raise_tool_error("timeout_seconds must be greater than or equal to 1")
        if poll_interval <= 0:
            _raise_tool_error("poll_interval must be greater than 0")

        deadline = monotonic() + timeout_seconds
        timed_out = False

        try:
            payload = CreateJobRequest(prompt=normalized_prompt, work_dir=work_dir)
            job = service.create_job(payload)

            while job.status not in TERMINAL_JOB_STATUSES:
                if monotonic() >= deadline:
                    timed_out = True
                    break

                sleep(poll_interval)
                job = service.get_job(job.job_id)

            if timed_out:
                job = service.get_job(job.job_id)
                timed_out = job.status not in TERMINAL_JOB_STATUSES

            aggregated_result = build_aggregated_job_result(job, timed_out=timed_out)
            response = RunCodexTaskResponse.model_validate(
                aggregated_result.model_dump(mode="python")
            )
            return build_data_result_tool_response(response)  # type: ignore[return-value]
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("run_codex_task tool failed")
            _raise_tool_error("Failed to run Codex task")

    @mcp.tool(
        name="get_result",
        description="Return the aggregated result for a job, preferring result.json when available.",
        structured_output=True,
    )
    def get_result(job_id: str) -> GetResultResponse:
        normalized_job_id = _require_non_empty(job_id, "job_id")

        try:
            job = service.get_job(normalized_job_id)
            aggregated_result, result_file_present = load_or_aggregate_result(
                job,
                repair_missing_result_file=True,
            )
            response = GetResultResponse.model_validate(
                {
                    **aggregated_result.model_dump(mode="python"),
                    "result_file_present": result_file_present,
                }
            )
            return build_data_result_tool_response(response)  # type: ignore[return-value]
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("get_result tool failed")
            _raise_tool_error("Failed to load aggregated result")

    @mcp.tool(
        name="get_latest_result",
        description="Return the aggregated result for the latest job by created_at and job_id.",
        structured_output=True,
    )
    def get_latest_result() -> GetLatestResultResponse:
        try:
            latest_job = service.get_latest_job()
            aggregated_result, result_file_present = load_or_aggregate_result(
                latest_job,
                repair_missing_result_file=True,
            )
            response = GetLatestResultResponse.model_validate(
                {
                    **aggregated_result.model_dump(mode="python"),
                    "result_file_present": result_file_present,
                    "resolved_job_id": latest_job.job_id,
                }
            )
            return build_data_result_tool_response(response)  # type: ignore[return-value]
        except HTTPException as exc:
            _handle_http_exception(exc)
        except ToolError:
            raise
        except Exception:
            logger.exception("get_latest_result tool failed")
            _raise_tool_error("Failed to load the latest aggregated result")

    @mcp.tool(
        name="render_result_widget",
        description="Render the read-only Codex result widget for a unified result payload.",
        meta=RESULT_WIDGET_TOOL_META,
        structured_output=True,
    )
    def render_result_widget(result: ResultWidgetPayload) -> ResultWidgetPayload:
        try:
            payload = ResultWidgetPayload.model_validate(
                result.model_dump(mode="python")
            )
            return build_render_result_widget_response(payload)  # type: ignore[return-value]
        except ToolError:
            raise
        except Exception:
            logger.exception("render_result_widget tool failed")
            _raise_tool_error("Failed to render result widget")
