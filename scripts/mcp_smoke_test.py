from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from local_smoke_test import DEFAULT_PROMPT, build_smoke_work_dir, print_section
from bridge_server.schemas import (
    GetLatestResultResponse,
    GetResultResponse,
    ResultWidgetPayload,
    RunCodexTaskResponse,
)
from mcp_server.result_widget import (
    RESULT_WIDGET_MIME_TYPE,
    RESULT_WIDGET_PAYLOAD_META_KEY,
    RESULT_WIDGET_URI,
    build_result_text_content,
    build_result_widget_payload,
)


DEFAULT_MCP_URL = "http://127.0.0.1:8001/mcp"
REQUIRED_TOOLS = {
    "create_job",
    "get_job",
    "get_result",
    "get_latest_result",
    "list_jobs",
    "get_artifact",
    "wait_for_job",
    "run_codex_task",
    "render_result_widget",
}
DATA_TOOLS = {
    "run_codex_task": RunCodexTaskResponse,
    "get_result": GetResultResponse,
    "get_latest_result": GetLatestResultResponse,
}
RENDER_TOOL_NAME = "render_result_widget"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local smoke test against the Codex bridge MCP server.")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL, help="Remote MCP endpoint URL.")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=120,
        help="Maximum time to wait for the job to reach a terminal state.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds while waiting for job completion.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Job prompt to submit. Defaults to a harmless demo prompt.",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="Optional work_dir override. If omitted, a fresh smoke_runs subdirectory is created automatically.",
    )
    return parser.parse_args()


def _extract_tool_error(result: Any) -> str:
    messages: list[str] = []
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if text:
            messages.append(text)
    if messages:
        return "\n".join(messages)
    return json.dumps(result.model_dump(), ensure_ascii=False)


def _require_success(result: Any, tool_name: str) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise RuntimeError(f"{tool_name} failed: {_extract_tool_error(result)}")

    payload = getattr(result, "structuredContent", None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{tool_name} returned unexpected structured content")
    return payload


def _require_meta(result: Any, tool_name: str) -> dict[str, Any]:
    payload = getattr(result, "meta", None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{tool_name} returned unexpected tool metadata")
    return payload


def _resolve_widget_uri(meta: dict[str, Any] | None) -> str | None:
    if not isinstance(meta, dict):
        return None

    output_template = meta.get("openai/outputTemplate")
    if isinstance(output_template, str) and output_template:
        return output_template

    ui = meta.get("ui")
    if isinstance(ui, dict):
        resource_uri = ui.get("resourceUri")
        if isinstance(resource_uri, str) and resource_uri:
            return resource_uri
    return None


def _require_text_output(result: Any, tool_name: str) -> str:
    texts: list[str] = []
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            texts.append(text)
    if not texts:
        raise RuntimeError(f"{tool_name} returned no text content")
    return "\n".join(texts)


def _assert_no_widget_tool_descriptor_binding(tool: Any) -> None:
    widget_uri = _resolve_widget_uri(getattr(tool, "meta", None))
    if widget_uri is not None:
        raise RuntimeError(
            f"{tool.name} unexpectedly registered widget metadata: {widget_uri!r}"
        )


def _assert_render_tool_descriptor_binding(tool: Any) -> None:
    widget_uri = _resolve_widget_uri(getattr(tool, "meta", None))
    if widget_uri != RESULT_WIDGET_URI:
        raise RuntimeError(
            f"{tool.name} registered unexpected widget resource: {widget_uri!r}"
        )


def _assert_no_widget_result_binding(
    *,
    tool_name: str,
    result: Any,
    payload: dict[str, Any],
) -> None:
    meta = getattr(result, "meta", None)
    widget_uri = _resolve_widget_uri(meta)
    if widget_uri is not None:
        raise RuntimeError(
            f"{tool_name} unexpectedly returned a widget resource: {widget_uri!r}"
        )
    if isinstance(meta, dict) and RESULT_WIDGET_PAYLOAD_META_KEY in meta:
        raise RuntimeError(f"{tool_name} unexpectedly returned {RESULT_WIDGET_PAYLOAD_META_KEY}")

    response_model = DATA_TOOLS[tool_name].model_validate(payload)
    expected_text = build_result_text_content(build_result_widget_payload(response_model))
    actual_text = _require_text_output(result, tool_name)
    if actual_text != expected_text:
        raise RuntimeError(f"{tool_name} returned unexpected text content")


def _assert_render_widget_binding(
    *,
    result: Any,
    expected_payload: dict[str, Any],
) -> None:
    meta = _require_meta(result, RENDER_TOOL_NAME)
    widget_payload = meta.get(RESULT_WIDGET_PAYLOAD_META_KEY)
    if not isinstance(widget_payload, dict):
        raise RuntimeError(f"{RENDER_TOOL_NAME} did not include {RESULT_WIDGET_PAYLOAD_META_KEY}")

    actual_payload = _require_success(result, RENDER_TOOL_NAME)
    if actual_payload != expected_payload:
        raise RuntimeError(f"{RENDER_TOOL_NAME} returned unexpected structured content")
    if widget_payload != expected_payload:
        raise RuntimeError(f"{RENDER_TOOL_NAME} returned a mismatched widget payload")

    rendered_text = _require_text_output(result, RENDER_TOOL_NAME)
    expected_job_id = expected_payload.get("job_id")
    if str(expected_job_id) not in rendered_text:
        raise RuntimeError(f"{RENDER_TOOL_NAME} returned unexpected text content")


def _require_widget_resource(resources: list[Any]) -> Any:
    for resource in resources:
        if str(getattr(resource, "uri", "")) == RESULT_WIDGET_URI:
            return resource
    raise RuntimeError(f"Missing widget resource: {RESULT_WIDGET_URI}")


async def run_smoke_test(args: argparse.Namespace) -> int:
    print(f"Connecting to MCP server at {args.mcp_url} ...")
    async with streamablehttp_client(args.mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            print(f"Initialized MCP session with server: {init_result.serverInfo.name}")

            list_tools_result = await session.list_tools()
            tool_names = [tool.name for tool in list_tools_result.tools]
            print(f"Registered tools: {', '.join(tool_names)}")

            missing_tools = sorted(REQUIRED_TOOLS.difference(tool_names))
            if missing_tools:
                raise RuntimeError(f"Required MCP tools are missing: {', '.join(missing_tools)}")

            for tool in list_tools_result.tools:
                if tool.name in DATA_TOOLS:
                    _assert_no_widget_tool_descriptor_binding(tool)
                if tool.name == RENDER_TOOL_NAME:
                    _assert_render_tool_descriptor_binding(tool)

            list_resources_result = await session.list_resources()
            widget_resource = _require_widget_resource(list_resources_result.resources)
            if getattr(widget_resource, "mimeType", None) != RESULT_WIDGET_MIME_TYPE:
                raise RuntimeError(
                    f"Widget resource mimeType mismatch: {getattr(widget_resource, 'mimeType', None)!r}"
                )

            read_widget_result = await session.read_resource(RESULT_WIDGET_URI)
            widget_contents = getattr(read_widget_result, "contents", [])
            if not widget_contents:
                raise RuntimeError("Widget resource returned no contents")
            widget_html = getattr(widget_contents[0], "text", "") or getattr(
                widget_contents[0], "content", ""
            )
            if "result-widget-root" not in widget_html:
                raise RuntimeError("Widget resource did not return the expected HTML shell")

            if args.work_dir:
                work_dir = Path(args.work_dir).expanduser().resolve()
                work_dir.mkdir(parents=True, exist_ok=True)
            else:
                work_dir = build_smoke_work_dir()

            print("Running demo job via MCP ...")
            task_result = await session.call_tool(
                "run_codex_task",
                {
                    "prompt": args.prompt,
                    "work_dir": str(work_dir),
                    "timeout_seconds": args.timeout_seconds,
                    "poll_interval": args.poll_interval,
                },
            )
            task = _require_success(task_result, "run_codex_task")
            _assert_no_widget_result_binding(
                tool_name="run_codex_task",
                result=task_result,
                payload=task,
            )
            job_id = task["job_id"]

            print(f"Job: {job_id}")
            print(f"Work dir: {work_dir}")
            print(f"Artifact dir: {task['artifact_dir']}")

            print_section(
                "Job Result",
                "\n".join(
                    [
                        f"job_id: {task.get('job_id')}",
                        f"status: {task.get('status')}",
                        f"timed_out: {task.get('timed_out')}",
                        f"return_code: {task.get('return_code')}",
                        f"command: {task.get('command')}",
                        f"duration_seconds: {task.get('duration_seconds')}",
                        f"work_dir: {task.get('work_dir')}",
                        f"artifact_dir: {task.get('artifact_dir')}",
                        f"artifact_names: {task.get('artifact_names')}",
                        f"summary: {task.get('summary')}",
                    ]
                ),
            )
            print_section("stdout_tail", task.get("stdout_tail", ""))
            print_section("stderr_tail", task.get("stderr_tail", ""))

            if task.get("timed_out"):
                print("\nMCP smoke test failed because run_codex_task timed out.", file=sys.stderr)
                return 2

            result_result = await session.call_tool(
                "get_result",
                {
                    "job_id": job_id,
                },
            )
            aggregated_result = _require_success(result_result, "get_result")
            _assert_no_widget_result_binding(
                tool_name="get_result",
                result=result_result,
                payload=aggregated_result,
            )
            latest_result = await session.call_tool("get_latest_result", {})
            latest_aggregated_result = _require_success(latest_result, "get_latest_result")
            _assert_no_widget_result_binding(
                tool_name="get_latest_result",
                result=latest_result,
                payload=latest_aggregated_result,
            )

            for source_tool_name, source_payload in (
                ("run_codex_task", task),
                ("get_result", aggregated_result),
                ("get_latest_result", latest_aggregated_result),
            ):
                render_payload = build_result_widget_payload(
                    DATA_TOOLS[source_tool_name].model_validate(source_payload)
                ).model_dump(mode="json")
                render_result = await session.call_tool(
                    RENDER_TOOL_NAME,
                    {"result": render_payload},
                )
                _assert_render_widget_binding(
                    result=render_result,
                    expected_payload=ResultWidgetPayload.model_validate(render_payload).model_dump(mode="json"),
                )

            print_section(
                "get_result",
                "\n".join(
                    [
                        f"job_id: {aggregated_result.get('job_id')}",
                        f"status: {aggregated_result.get('status')}",
                        f"result_file_present: {aggregated_result.get('result_file_present')}",
                        f"artifact_names: {aggregated_result.get('artifact_names')}",
                    ]
                ),
            )
            print_section(
                "get_latest_result",
                "\n".join(
                    [
                        f"resolved_job_id: {latest_aggregated_result.get('resolved_job_id')}",
                        f"status: {latest_aggregated_result.get('status')}",
                        f"result_file_present: {latest_aggregated_result.get('result_file_present')}",
                        f"artifact_names: {latest_aggregated_result.get('artifact_names')}",
                    ]
                ),
            )

            if aggregated_result.get("job_id") != job_id:
                print("\nMCP smoke test failed because get_result returned a different job_id.", file=sys.stderr)
                return 1

            if latest_aggregated_result.get("resolved_job_id") != job_id:
                print(
                    "\nMCP smoke test failed because get_latest_result did not resolve to the newest smoke-test job.",
                    file=sys.stderr,
                )
                return 1

            artifact_names = aggregated_result.get("artifact_names") or []
            latest_artifact_names = latest_aggregated_result.get("artifact_names") or []
            if "result.json" not in artifact_names or "result.json" not in latest_artifact_names:
                print("\nMCP smoke test failed because result.json was not present in aggregated artifacts.", file=sys.stderr)
                return 1

            for payload_name, payload in (
                ("get_result", aggregated_result),
                ("get_latest_result", latest_aggregated_result),
            ):
                for field_name in ("summary", "stdout_tail", "stderr_tail"):
                    if field_name not in payload:
                        print(
                            f"\nMCP smoke test failed because {payload_name} did not include {field_name}.",
                            file=sys.stderr,
                        )
                        return 1

            if not aggregated_result.get("result_file_present"):
                print("get_result used fallback aggregation before reading result.json.")
            if not latest_aggregated_result.get("result_file_present"):
                print("get_latest_result used fallback aggregation before reading result.json.")

            if task.get("status") == "succeeded":
                print("\nMCP smoke test passed.")
                return 0

            print("\nMCP smoke test failed because the job finished in failed state.", file=sys.stderr)
            return 1


def main() -> int:
    args = parse_args()
    try:
        return anyio.run(run_smoke_test, args)
    except RuntimeError as exc:
        print(f"MCP smoke test error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"MCP smoke test unexpected error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
