from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request
from uuid import uuid4


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_PROMPT = (
    "In the current working directory, create exactly one file named "
    "worker_demo_note.txt containing one or two short sentences that say it was "
    "created by the Codex bridge demo. Do not modify any other file. Then print "
    "the directory file list and briefly state what you changed."
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_smoke_work_dir() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = uuid4().hex[:8]
    work_dir = _project_root() / "data" / "demo_workspace" / "smoke_runs" / f"{timestamp}-{run_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local smoke test against the Codex bridge REST API.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Bridge server base URL.")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Maximum time to wait for the job to reach a terminal state.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
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


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def _format_http_error(exc: error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace").strip()
    if body:
        return f"HTTP {exc.code} for {exc.url}: {body}"
    return f"HTTP {exc.code} for {exc.url}"


def request_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with request.urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        raise RuntimeError(_format_http_error(exc)) from exc
    except error.URLError as exc:
        raise RuntimeError(f"request to {url} failed: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"response from {url} is not valid JSON: {raw}") from exc


def request_text(url: str) -> str:
    req = request.Request(url=url, method="GET")
    try:
        with request.urlopen(req, timeout=10) as response:
            return response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raise RuntimeError(_format_http_error(exc)) from exc
    except error.URLError as exc:
        raise RuntimeError(f"request to {url} failed: {exc.reason}") from exc


def print_section(title: str, content: str) -> None:
    print(f"\n=== {title} ===")
    print(content if content else "(empty)")


def run_smoke_test(args: argparse.Namespace) -> int:
    base_url = args.base_url.rstrip("/")
    print(f"Checking health at {base_url}/health ...")
    health = request_json("GET", _join_url(base_url, "/health"))
    if health.get("status") != "ok" or not health.get("database_ok"):
        print(f"Health check failed: {json.dumps(health, ensure_ascii=False)}", file=sys.stderr)
        return 3
    print("Health check passed.")

    if args.work_dir:
        work_dir = Path(args.work_dir).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = build_smoke_work_dir()

    payload: dict[str, Any] = {
        "prompt": args.prompt,
        "work_dir": str(work_dir),
    }

    print("Creating demo job ...")
    job = request_json("POST", _join_url(base_url, "/jobs"), payload)
    job_id = job["job_id"]
    print(f"Created job: {job_id}")
    print(f"Work dir: {work_dir}")
    print(f"Artifact dir: {job['artifact_dir']}")

    deadline = time.monotonic() + args.timeout_seconds
    last_status: str | None = None

    while True:
        current = request_json("GET", _join_url(base_url, f"/jobs/{job_id}"))
        status = current.get("status")
        if status != last_status:
            print(f"Job status: {status}")
            last_status = status

        if status in {"succeeded", "failed"}:
            break

        if time.monotonic() >= deadline:
            print(
                f"Timed out after {args.timeout_seconds} seconds waiting for job {job_id}. "
                f"Last observed status: {status}",
                file=sys.stderr,
            )
            return 2

        time.sleep(args.poll_interval)

    print_section(
        "Job Result",
        "\n".join(
            [
                f"job_id: {current.get('job_id')}",
                f"status: {current.get('status')}",
                f"return_code: {current.get('return_code')}",
                f"command: {current.get('command')}",
                f"work_dir: {current.get('work_dir')}",
                f"artifact_dir: {current.get('artifact_dir')}",
                f"summary: {current.get('summary')}",
            ]
        ),
    )

    summary_text = request_text(_join_url(base_url, f"/jobs/{job_id}/artifacts/summary.txt"))
    stdout_text = request_text(_join_url(base_url, f"/jobs/{job_id}/artifacts/stdout.log"))
    print_section("summary.txt", summary_text)
    print_section("stdout.log", stdout_text)

    if current.get("status") == "succeeded":
        print("\nSmoke test passed.")
        return 0

    print("\nSmoke test failed because the job finished in failed state.", file=sys.stderr)
    return 1


def main() -> int:
    args = parse_args()
    try:
        return run_smoke_test(args)
    except RuntimeError as exc:
        print(f"Smoke test error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
