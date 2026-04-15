from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Final, TypeAlias

import mcp.types as types
from mcp.server.fastmcp import FastMCP

from bridge_server.schemas import (
    GetLatestResultResponse,
    GetResultResponse,
    ResultWidgetPayload,
    RunCodexTaskResponse,
)


RESULT_WIDGET_URI: Final[str] = "ui://widget/codex-result-panel.html"
RESULT_WIDGET_MIME_TYPE: Final[str] = "text/html;profile=mcp-app"
RESULT_WIDGET_PAYLOAD_META_KEY: Final[str] = "resultWidgetPayload"
RESULT_WIDGET_RESOURCE_META: Final[dict[str, object]] = {
    "openai/widgetDescription": (
        "Displays a read-only Codex job result panel with status, paths, artifacts, "
        "summary, stdout tail, and stderr tail."
    ),
    "openai/widgetPrefersBorder": True,
    "openai/widgetCSP": {
        "connect_domains": [],
        "resource_domains": [],
    },
}
RESULT_WIDGET_TOOL_META: Final[dict[str, object]] = {
    "ui": {"resourceUri": RESULT_WIDGET_URI},
    "openai/outputTemplate": RESULT_WIDGET_URI,
    "openai/widgetAccessible": False,
    "openai/toolInvocation/invoking": "Loading Codex result panel",
    "openai/toolInvocation/invoked": "Codex result panel ready",
}
RESULT_WIDGET_TEXT_TEMPLATE: Final[str] = (
    "job_id: {job_id}\n"
    "status: {status}\n"
    "timed_out: {timed_out}\n"
    "result_file_present: {result_file_present}\n"
    "resolved_job_id: {resolved_job_id}\n"
    "return_code: {return_code}\n"
    "duration_seconds: {duration_seconds}\n"
    "work_dir: {work_dir}\n"
    "artifact_dir: {artifact_dir}\n"
    "artifact_names: {artifact_names}\n"
    "summary: {summary}\n"
    "stdout_tail: {stdout_tail}\n"
    "stderr_tail: {stderr_tail}"
)

ResultToolResponse: TypeAlias = (
    RunCodexTaskResponse | GetResultResponse | GetLatestResultResponse
)


def register_result_widget_resource(mcp: FastMCP) -> None:
    @mcp.resource(
        RESULT_WIDGET_URI,
        name="codex_result_widget",
        title="Codex Result Panel",
        description="Read-only widget for a single aggregated Codex bridge job result.",
        mime_type=RESULT_WIDGET_MIME_TYPE,
        meta=RESULT_WIDGET_RESOURCE_META,
    )
    def result_widget() -> str:
        return load_result_widget_html()


@lru_cache(maxsize=1)
def load_result_widget_html() -> str:
    widget_path = Path(__file__).resolve().parent / "ui" / "result_widget.html"
    return widget_path.read_text(encoding="utf-8")


def build_result_widget_payload(response: ResultToolResponse) -> ResultWidgetPayload:
    return ResultWidgetPayload(
        job_id=response.job_id,
        status=response.status,
        timed_out=getattr(response, "timed_out", None),
        result_file_present=getattr(response, "result_file_present", None),
        resolved_job_id=getattr(response, "resolved_job_id", None),
        summary=response.summary,
        stdout_tail=response.stdout_tail,
        stderr_tail=response.stderr_tail,
        work_dir=response.work_dir,
        artifact_dir=response.artifact_dir,
        artifact_names=list(response.artifact_names),
        return_code=response.return_code,
        command=response.command,
        duration_seconds=response.duration_seconds,
        created_at=getattr(response, "created_at", None),
        started_at=getattr(response, "started_at", None),
        finished_at=getattr(response, "finished_at", None),
    )


def build_result_text_content(payload: ResultWidgetPayload) -> str:
    return _build_result_text_content(payload.model_dump(mode="json"))


def build_data_result_tool_response(
    response: ResultToolResponse,
    *,
    widget_payload: ResultWidgetPayload | None = None,
) -> types.CallToolResult:
    resolved_widget_payload = widget_payload or build_result_widget_payload(response)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=build_result_text_content(resolved_widget_payload),
            )
        ],
        structuredContent=response.model_dump(mode="json"),
        isError=False,
    )


def build_render_result_widget_response(payload: ResultWidgetPayload) -> types.CallToolResult:
    payload_json = payload.model_dump(mode="json")
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=f"Rendered Codex result widget for job {payload.job_id}.",
            )
        ],
        structuredContent=payload_json,
        _meta={
            RESULT_WIDGET_PAYLOAD_META_KEY: payload_json,
        },
        isError=False,
    )


def _build_result_text_content(widget_payload: dict[str, object]) -> str:
    def display(value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return RESULT_WIDGET_TEXT_TEMPLATE.format(
        job_id=display(widget_payload.get("job_id")),
        status=display(widget_payload.get("status")),
        timed_out=display(widget_payload.get("timed_out")),
        result_file_present=display(widget_payload.get("result_file_present")),
        resolved_job_id=display(widget_payload.get("resolved_job_id")),
        return_code=display(widget_payload.get("return_code")),
        duration_seconds=display(widget_payload.get("duration_seconds")),
        work_dir=display(widget_payload.get("work_dir")),
        artifact_dir=display(widget_payload.get("artifact_dir")),
        artifact_names=display(widget_payload.get("artifact_names")),
        summary=display(widget_payload.get("summary")),
        stdout_tail=display(widget_payload.get("stdout_tail")),
        stderr_tail=display(widget_payload.get("stderr_tail")),
    )
