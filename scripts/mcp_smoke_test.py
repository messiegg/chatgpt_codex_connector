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
}


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
            latest_result = await session.call_tool("get_latest_result", {})
            latest_aggregated_result = _require_success(latest_result, "get_latest_result")

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
