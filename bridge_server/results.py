from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from bridge_server.schemas import AggregatedJobResult


logger = logging.getLogger(__name__)

SUMMARY_ARTIFACT_NAME = "summary.txt"
STDOUT_ARTIFACT_NAME = "stdout.log"
STDERR_ARTIFACT_NAME = "stderr.log"
METADATA_ARTIFACT_NAME = "metadata.json"
RESULT_ARTIFACT_NAME = "result.json"
TAIL_MAX_CHARS = 4_000
TAIL_MAX_LINES = 40
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class JobLike(Protocol):
    job_id: str
    status: Any
    summary: str | None
    work_dir: str
    created_at: str
    artifact_dir: str
    started_at: str | None
    finished_at: str | None
    return_code: int | None
    command: str | None


def tail_text(
    value: str | None,
    *,
    max_lines: int = TAIL_MAX_LINES,
    max_chars: int = TAIL_MAX_CHARS,
) -> str:
    if not value:
        return ""

    lines = value.rstrip().splitlines()
    if not lines:
        return ""

    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
        if "\n" in tail:
            tail = tail.split("\n", 1)[1]
    return tail.strip()


def artifact_dir_path(artifact_dir: str | Path) -> Path:
    return Path(artifact_dir).expanduser().resolve()


def list_artifact_names(artifact_dir: str | Path) -> list[str]:
    resolved_artifact_dir = artifact_dir_path(artifact_dir)
    if not resolved_artifact_dir.is_dir():
        return []

    return sorted(
        child.name
        for child in resolved_artifact_dir.iterdir()
        if child.is_file()
    )


def is_terminal_job_status(status: Any) -> bool:
    return _status_value(status) in TERMINAL_JOB_STATUSES


def read_optional_text(path: Path) -> str | None:
    if not path.is_file():
        return None

    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.exception("failed to read text file %s", path)
        return None


def read_optional_json(path: Path) -> dict[str, Any] | None:
    raw_text = read_optional_text(path)
    if raw_text is None:
        return None

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.warning("JSON file %s is invalid", path)
        return None

    if not isinstance(payload, dict):
        logger.warning("JSON file %s did not contain an object", path)
        return None
    return payload


def parse_duration_from_job_timestamps(
    started_at: str | None,
    finished_at: str | None,
) -> float | None:
    if started_at is None:
        return None

    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at) if finished_at else datetime.now(timezone.utc)
    except ValueError:
        return None

    return round(max((finished - started).total_seconds(), 0.0), 3)


def build_aggregated_job_result(
    job: JobLike,
    *,
    timed_out: bool | None = None,
    artifact_names_override: list[str] | None = None,
) -> AggregatedJobResult:
    artifact_dir = artifact_dir_path(job.artifact_dir)
    metadata = read_optional_json(artifact_dir / METADATA_ARTIFACT_NAME)
    summary_text = read_optional_text(artifact_dir / SUMMARY_ARTIFACT_NAME)
    stdout_text = read_optional_text(artifact_dir / STDOUT_ARTIFACT_NAME)
    stderr_text = read_optional_text(artifact_dir / STDERR_ARTIFACT_NAME)

    duration_seconds = _coerce_float(metadata.get("duration_seconds")) if metadata is not None else None
    command = _coerce_str(metadata.get("command")) if metadata is not None else None
    work_dir = _coerce_str(metadata.get("work_dir")) if metadata is not None else None
    return_code = _coerce_int(metadata.get("return_code")) if metadata is not None else None

    if duration_seconds is None:
        duration_seconds = parse_duration_from_job_timestamps(job.started_at, job.finished_at)
    if command is None:
        command = job.command
    if work_dir is None:
        work_dir = job.work_dir
    if return_code is None:
        return_code = job.return_code

    artifact_names = artifact_names_override if artifact_names_override is not None else list_artifact_names(artifact_dir)

    return AggregatedJobResult(
        job_id=job.job_id,
        status=_status_value(job.status),
        summary=(summary_text or job.summary or "").strip(),
        stdout_tail=tail_text(stdout_text),
        stderr_tail=tail_text(stderr_text),
        work_dir=work_dir,
        artifact_dir=str(artifact_dir),
        artifact_names=sorted(artifact_names),
        return_code=return_code,
        command=command,
        duration_seconds=duration_seconds,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        timed_out=timed_out,
        metadata=metadata,
    )


def write_result_file(job: JobLike, *, timed_out: bool | None = None) -> AggregatedJobResult:
    artifact_dir = artifact_dir_path(job.artifact_dir)
    result_path = artifact_dir / RESULT_ARTIFACT_NAME
    artifact_names = sorted(set(list_artifact_names(artifact_dir) + [RESULT_ARTIFACT_NAME]))
    result = build_aggregated_job_result(
        job,
        timed_out=timed_out,
        artifact_names_override=artifact_names,
    )
    result_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def load_result_file(job: JobLike) -> AggregatedJobResult | None:
    artifact_dir = artifact_dir_path(job.artifact_dir)
    payload = read_optional_json(artifact_dir / RESULT_ARTIFACT_NAME)
    if payload is None:
        return None

    try:
        stored_result = AggregatedJobResult.model_validate(payload)
    except ValidationError:
        logger.warning("result.json for job %s did not match the expected schema", job.job_id)
        return None

    repaired_artifact_names = sorted(set(list_artifact_names(artifact_dir) + [RESULT_ARTIFACT_NAME]))
    return stored_result.model_copy(
        update={
            "job_id": job.job_id,
            "status": _status_value(job.status),
            "artifact_dir": str(artifact_dir),
            "artifact_names": repaired_artifact_names,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
    )


def load_or_aggregate_result(
    job: JobLike,
    *,
    timed_out: bool | None = None,
    repair_missing_result_file: bool = False,
) -> tuple[AggregatedJobResult, bool]:
    stored_result = load_result_file(job)
    if stored_result is not None:
        if timed_out is not None:
            stored_result = stored_result.model_copy(update={"timed_out": timed_out})
        return stored_result, True

    aggregated_result = build_aggregated_job_result(job, timed_out=timed_out)
    if repair_missing_result_file and is_terminal_job_status(job.status):
        try:
            aggregated_result = write_result_file(job, timed_out=timed_out)
        except Exception:
            logger.exception("failed to repair result.json for job %s", job.job_id)

    return aggregated_result, False


def _status_value(status: Any) -> str:
    if isinstance(status, str):
        return status
    value = getattr(status, "value", None)
    if isinstance(value, str):
        return value
    return str(status)


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 3)
    return None


def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
