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
REQUIRED_TOOLS = {"create_job", "get_job", "list_jobs", "get_artifact", "wait_for_job"}


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

            print("Creating demo job via MCP ...")
            create_result = await session.call_tool(
                "create_job",
                {
                    "prompt": args.prompt,
                    "work_dir": str(work_dir),
                },
            )
            created_job = _require_success(create_result, "create_job")
            job_id = created_job["job_id"]

            print(f"Created job: {job_id}")
            print(f"Work dir: {work_dir}")
            print(f"Artifact dir: {created_job['artifact_dir']}")

            print("Waiting for job via MCP ...")
            wait_result = await session.call_tool(
                "wait_for_job",
                {
                    "job_id": job_id,
                    "timeout_seconds": args.timeout_seconds,
                    "poll_interval": args.poll_interval,
                },
            )
            final_job = _require_success(wait_result, "wait_for_job")

            print_section(
                "Job Result",
                "\n".join(
                    [
                        f"job_id: {final_job.get('job_id')}",
                        f"status: {final_job.get('status')}",
                        f"return_code: {final_job.get('return_code')}",
                        f"command: {final_job.get('command')}",
                        f"work_dir: {final_job.get('work_dir')}",
                        f"artifact_dir: {final_job.get('artifact_dir')}",
                        f"summary: {final_job.get('summary')}",
                    ]
                ),
            )

            artifact_result = await session.call_tool(
                "get_artifact",
                {
                    "job_id": job_id,
                    "name": "summary.txt",
                },
            )
            artifact = _require_success(artifact_result, "get_artifact")
            print_section("summary.txt", artifact["content"])

            if final_job.get("status") == "succeeded":
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
