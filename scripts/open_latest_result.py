from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from bridge_server.config import BridgeSettings
from bridge_server.results import load_or_aggregate_result, tail_text
from bridge_server.schemas import GetLatestResultResponse, GetResultResponse
from bridge_server.service import JobService
from storage import JobRepository, SQLiteDatabase


DISPLAY_MAX_CHARS = 2_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the latest aggregated Codex bridge result.")
    parser.add_argument("--work-dir", action="store_true", help="Open the job work_dir instead of artifact_dir.")
    parser.add_argument("--print-json", action="store_true", help="Print the full aggregated JSON payload.")
    parser.add_argument("--job-id", default=None, help="Inspect a specific job instead of the latest job.")
    parser.add_argument("--no-open", action="store_true", help="Only print the result; do not open a directory.")
    return parser.parse_args()


def build_service() -> JobService:
    settings = BridgeSettings.from_env()
    database = SQLiteDatabase(settings.database_path)
    repository = JobRepository(database)
    service = JobService(settings, repository)
    service.initialize()
    return service


def load_result_payload(service: JobService, job_id: str | None) -> GetResultResponse | GetLatestResultResponse:
    if job_id is not None:
        job = service.get_job(job_id)
        aggregated_result, result_file_present = load_or_aggregate_result(
            job,
            repair_missing_result_file=True,
        )
        return GetResultResponse.model_validate(
            {
                **aggregated_result.model_dump(mode="python"),
                "result_file_present": result_file_present,
            }
        )

    latest_job = service.get_latest_job()
    aggregated_result, result_file_present = load_or_aggregate_result(
        latest_job,
        repair_missing_result_file=True,
    )
    return GetLatestResultResponse.model_validate(
        {
            **aggregated_result.model_dump(mode="python"),
            "result_file_present": result_file_present,
            "resolved_job_id": latest_job.job_id,
        }
    )


def print_human_result(payload: GetResultResponse | GetLatestResultResponse) -> None:
    print(f"job_id: {payload.job_id}")
    if isinstance(payload, GetLatestResultResponse):
        print(f"resolved_job_id: {payload.resolved_job_id}")
    print(f"status: {payload.status}")
    print(f"work_dir: {payload.work_dir}")
    print(f"artifact_dir: {payload.artifact_dir}")
    print(f"return_code: {payload.return_code}")
    print(f"duration_seconds: {payload.duration_seconds}")
    print(f"result_file_present: {payload.result_file_present}")
    print(f"artifact_names: {', '.join(payload.artifact_names) if payload.artifact_names else '(empty)'}")
    print()
    print("summary:")
    print(_format_display_text(payload.summary))
    print()
    print("stdout_tail:")
    print(_format_display_text(payload.stdout_tail))
    print()
    print("stderr_tail:")
    print(_format_display_text(payload.stderr_tail))


def _format_display_text(value: str) -> str:
    if not value:
        return "(empty)"
    if len(value) <= DISPLAY_MAX_CHARS:
        return value
    return tail_text(value, max_chars=DISPLAY_MAX_CHARS)


def open_directory(path: str) -> None:
    resolved_path = Path(path).expanduser().resolve()
    if not resolved_path.exists():
        print(f"Open skipped: path does not exist: {resolved_path}")
        return

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(resolved_path)])
        elif os.name == "nt":
            os.startfile(str(resolved_path))  # type: ignore[attr-defined]
        else:
            opener = shutil.which("xdg-open")
            if opener is None:
                print(f"Open skipped: no opener found for {resolved_path}")
                return
            subprocess.Popen([opener, str(resolved_path)])
    except Exception as exc:
        print(f"Open skipped: failed to open {resolved_path}: {exc}")


def main() -> int:
    args = parse_args()

    try:
        service = build_service()
        payload = load_result_payload(service, args.job_id)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.print_json:
        print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        print_human_result(payload)

    if args.no_open:
        return 0

    target_path = payload.work_dir if args.work_dir else payload.artifact_dir
    open_directory(target_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
