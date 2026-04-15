from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dev_common import PROJECT_ROOT

from bridge_server.config import BridgeSettings, is_path_within_allowed_roots
from bridge_server.results import (
    METADATA_ARTIFACT_NAME,
    load_or_aggregate_result,
    read_optional_json,
    read_optional_text,
)
from bridge_server.schemas import CreateJobRequest, JobResponse
from bridge_server.service import JobService
from storage import JobRecord, JobRepository, JobStatus, SQLiteDatabase


ENV_PATH = PROJECT_ROOT / ".env"
SESSION_FILENAME = "dev_session.json"
BRIDGE_ENV_PREFIXES = ("BRIDGE_", "CODEX_BRIDGE_")
PROMPT_ARTIFACT_NAME = "prompt.txt"
JOB_STATUS_FILTER_OPTIONS = ("all", "queued", "running", "succeeded", "failed", "cancelled")
SERVER_LOG_FILENAME = "dev_mcp_server.log"
TUNNEL_LOG_FILENAME = "dev_tunnel.log"
UNAVAILABLE_TEXT = "(unavailable)"
EMPTY_TEXT = "(empty)"
_REPOSITORY_JOB_STATUS_BY_VALUE = {
    status.value: status
    for status in JobStatus
}


@dataclass(slots=True)
class ServiceStatus:
    state: str
    health_url: str
    local_mcp_url: str
    public_mcp_url: str
    developer_mode_address: str
    logs_path: str
    artifacts_path: str
    session_file_path: str
    server_log_path: str
    tunnel_log_path: str
    mcp_server_pid: str
    tunnel_pid: str
    session_payload: dict[str, object] | None = None


def _parse_env_lines(lines: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        normalized_value = value.strip()
        if (
            normalized_value
            and normalized_value[0] == normalized_value[-1]
            and normalized_value[0] in {"'", '"'}
        ):
            normalized_value = normalized_value[1:-1]
        values[key] = normalized_value
    return values


def parse_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    if not path.is_file():
        return {}
    return _parse_env_lines(path.read_text(encoding="utf-8").splitlines())


@contextmanager
def bridge_environment_from_file(path: Path = ENV_PATH) -> Iterator[None]:
    env_values = parse_env_file(path)
    previous_values: dict[str, str | None] = {}
    affected_keys = {
        key
        for key in os.environ
        if key.startswith(BRIDGE_ENV_PREFIXES)
    }
    affected_keys.update(
        key
        for key in env_values
        if key.startswith(BRIDGE_ENV_PREFIXES)
    )

    for key in affected_keys:
        previous_values[key] = os.environ.get(key)
        os.environ.pop(key, None)

    os.environ.update(env_values)
    try:
        yield
    finally:
        for key in affected_keys:
            os.environ.pop(key, None)
            previous_value = previous_values[key]
            if previous_value is not None:
                os.environ[key] = previous_value


def load_settings(path: Path = ENV_PATH) -> BridgeSettings:
    with bridge_environment_from_file(path):
        return BridgeSettings.from_env()


def normalize_roots(paths: Sequence[str | Path]) -> tuple[Path, ...]:
    normalized_roots: list[Path] = []
    seen_roots: set[Path] = set()
    for raw_path in paths:
        resolved_path = Path(raw_path).expanduser().resolve()
        if resolved_path in seen_roots:
            continue
        seen_roots.add(resolved_path)
        normalized_roots.append(resolved_path)
    return tuple(normalized_roots)


def validate_root_configuration(
    roots: Sequence[str | Path],
    default_work_dir: str | Path,
) -> tuple[tuple[Path, ...], Path]:
    normalized_roots = normalize_roots(roots)
    if not normalized_roots:
        raise ValueError("allowed work roots must not be empty")

    resolved_default_work_dir = Path(default_work_dir).expanduser().resolve()
    if not is_path_within_allowed_roots(resolved_default_work_dir, normalized_roots):
        raise ValueError("default_work_dir must be within one of the allowed work roots")

    return normalized_roots, resolved_default_work_dir


def update_env_file(
    updates: dict[str, str],
    *,
    path: Path = ENV_PATH,
) -> None:
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining_updates = dict(updates)
    updated_lines: list[str] = []

    for raw_line in existing_lines:
        stripped_line = raw_line.strip()
        if not stripped_line or stripped_line.startswith("#") or "=" not in raw_line:
            updated_lines.append(raw_line)
            continue

        key, _ = raw_line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in remaining_updates:
            updated_lines.append(f"{normalized_key}={remaining_updates.pop(normalized_key)}")
        else:
            updated_lines.append(raw_line)

    if updated_lines and updated_lines[-1] != "":
        updated_lines.append("")

    for key, value in remaining_updates.items():
        updated_lines.append(f"{key}={value}")

    output = "\n".join(updated_lines).rstrip() + "\n"
    path.write_text(output, encoding="utf-8")


def save_root_configuration(
    roots: Sequence[str | Path],
    default_work_dir: str | Path,
    *,
    path: Path = ENV_PATH,
) -> tuple[tuple[Path, ...], Path]:
    normalized_roots, resolved_default_work_dir = validate_root_configuration(roots, default_work_dir)
    update_env_file(
        {
            "BRIDGE_ALLOWED_WORK_ROOTS": json.dumps(
                [str(root) for root in normalized_roots],
                ensure_ascii=False,
            ),
            "BRIDGE_DEFAULT_WORK_DIR": str(resolved_default_work_dir),
        },
        path=path,
    )
    return normalized_roots, resolved_default_work_dir


def read_session_payload(session_path: Path) -> dict[str, object] | None:
    if not session_path.is_file():
        return None

    try:
        payload = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def load_dev_session(settings: BridgeSettings) -> dict[str, object] | None:
    return read_session_payload(settings.logs_dir / SESSION_FILENAME)


def _http_get_json(url: str, *, timeout_seconds: float = 2.0) -> dict[str, object] | None:
    try:
        with request.urlopen(url, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except (error.HTTPError, error.URLError, TimeoutError):
        return None

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def load_service_status(settings: BridgeSettings) -> ServiceStatus:
    session_path = settings.logs_dir / SESSION_FILENAME
    session_payload = load_dev_session(settings)
    default_health_url = f"http://{settings.mcp_host}:{settings.mcp_port}/health"
    default_local_mcp_url = f"http://{settings.mcp_host}:{settings.mcp_port}{settings.mcp_path}"
    default_server_log_path = (settings.logs_dir / SERVER_LOG_FILENAME).expanduser().resolve()
    default_tunnel_log_path = (settings.logs_dir / TUNNEL_LOG_FILENAME).expanduser().resolve()

    health_url = _as_text(session_payload.get("health_url")) if session_payload else default_health_url
    if not health_url:
        health_url = default_health_url

    health_payload = _http_get_json(health_url)
    state = "running" if health_payload and health_payload.get("status") == "ok" else "stopped"

    local_mcp_url = _as_text(session_payload.get("local_mcp_url")) if session_payload else default_local_mcp_url
    public_mcp_url = _as_text(session_payload.get("public_mcp_url")) if session_payload else None
    logs_path = settings.logs_dir
    artifacts_path = settings.artifacts_dir
    server_log_path = default_server_log_path
    tunnel_log_path = default_tunnel_log_path
    mcp_server_pid = _as_pid_text(session_payload.get("mcp_server_pid")) if session_payload else UNAVAILABLE_TEXT
    tunnel_pid = _as_pid_text(session_payload.get("tunnel_pid")) if session_payload else UNAVAILABLE_TEXT

    if session_payload is not None:
        server_log_path = Path(
            _as_text(session_payload.get("server_log_path")) or default_server_log_path
        ).expanduser().resolve()
        tunnel_log_path = Path(
            _as_text(session_payload.get("tunnel_log_path")) or default_tunnel_log_path
        ).expanduser().resolve()
        logs_path = server_log_path.parent
        artifacts_path = Path(
            _as_text(session_payload.get("artifacts_dir")) or settings.artifacts_dir
        ).expanduser().resolve()

    normalized_public_mcp_url = public_mcp_url or UNAVAILABLE_TEXT
    return ServiceStatus(
        state=state,
        health_url=health_url or UNAVAILABLE_TEXT,
        local_mcp_url=local_mcp_url or default_local_mcp_url,
        public_mcp_url=normalized_public_mcp_url,
        developer_mode_address=derive_developer_mode_address(normalized_public_mcp_url),
        logs_path=str(logs_path),
        artifacts_path=str(artifacts_path),
        session_file_path=str(session_path),
        server_log_path=str(server_log_path) if server_log_path else UNAVAILABLE_TEXT,
        tunnel_log_path=str(tunnel_log_path) if tunnel_log_path else UNAVAILABLE_TEXT,
        mcp_server_pid=mcp_server_pid,
        tunnel_pid=tunnel_pid,
        session_payload=session_payload,
    )


def build_repository(settings: BridgeSettings) -> JobRepository:
    return JobRepository(SQLiteDatabase(settings.database_path))


def build_job_service(settings: BridgeSettings) -> JobService:
    return JobService(settings, build_repository(settings))


def create_job_with_service(settings: BridgeSettings, request: CreateJobRequest) -> JobResponse:
    service = build_job_service(settings)
    return service.create_job(request)


def normalize_job_status_filter(status_filter: str | None) -> str:
    normalized_status = (status_filter or "all").strip().lower()
    if not normalized_status:
        normalized_status = "all"
    if normalized_status not in JOB_STATUS_FILTER_OPTIONS:
        raise ValueError(f"Unsupported status filter '{normalized_status}'")
    return normalized_status


def load_recent_jobs(
    settings: BridgeSettings,
    *,
    limit: int = 30,
    status_filter: str = "all",
) -> list[JobRecord]:
    normalized_status = normalize_job_status_filter(status_filter)
    repository = build_repository(settings)
    if normalized_status == "cancelled":
        return []

    parsed_status = _REPOSITORY_JOB_STATUS_BY_VALUE.get(normalized_status)
    return repository.list_jobs(status=parsed_status, limit=limit, offset=0)


def load_job_result(settings: BridgeSettings, job_id: str):
    repository = build_repository(settings)
    job = repository.get_job(job_id)
    if job is None:
        raise ValueError(f"Job '{job_id}' not found")
    result, result_file_present = load_or_aggregate_result(job)
    return job, result, result_file_present


def load_artifact_text(artifact_dir: str | Path, artifact_name: str) -> str:
    artifact_path = Path(artifact_dir).expanduser().resolve() / artifact_name
    return read_optional_text(artifact_path) or ""


def load_job_prompt_text(job: JobRecord) -> str:
    return load_artifact_text(job.artifact_dir, PROMPT_ARTIFACT_NAME)


def load_job_metadata_text(
    artifact_dir: str | Path,
    *,
    fallback_payload: dict[str, Any] | None = None,
) -> str:
    payload = fallback_payload
    if payload is None:
        payload = read_optional_json(Path(artifact_dir).expanduser().resolve() / METADATA_ARTIFACT_NAME)
    if payload is None:
        return "(empty)"
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_artifact_paths(
    artifact_dir: str | Path,
    artifact_names: Sequence[str],
) -> list[tuple[str, Path]]:
    resolved_artifact_dir = Path(artifact_dir).expanduser().resolve()
    artifact_paths: list[tuple[str, Path]] = []
    for artifact_name in artifact_names:
        if not artifact_name:
            continue
        candidate = (resolved_artifact_dir / artifact_name).resolve()
        if not candidate.is_relative_to(resolved_artifact_dir):
            continue
        artifact_paths.append((artifact_name, candidate))
    return artifact_paths


def derive_developer_mode_address(public_mcp_url: str | None) -> str:
    normalized_url = (public_mcp_url or "").strip()
    if not normalized_url or normalized_url == UNAVAILABLE_TEXT:
        return UNAVAILABLE_TEXT
    return normalized_url


def read_text_file_tail(
    path: str | Path | None,
    *,
    max_chars: int = 12_000,
) -> str:
    if path is None:
        return EMPTY_TEXT

    resolved_path = Path(path).expanduser().resolve()
    if not resolved_path.is_file():
        return EMPTY_TEXT

    try:
        content = resolved_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return EMPTY_TEXT

    if not content:
        return EMPTY_TEXT

    if len(content) <= max_chars:
        return content

    tail = content[-max_chars:]
    if "\n" in tail:
        tail = tail.split("\n", 1)[1]
    return tail or EMPTY_TEXT


def build_service_summary_text(
    status: ServiceStatus,
    *,
    refreshed_at: str,
) -> str:
    lines = [
        f"Current service state: {status.state}",
        f"Local health URL: {status.health_url}",
        f"Local MCP URL: {status.local_mcp_url}",
        f"Public MCP URL: {status.public_mcp_url}",
        f"ChatGPT Developer Mode address: {status.developer_mode_address}",
        f"MCP server PID: {status.mcp_server_pid}",
        f"Tunnel PID: {status.tunnel_pid}",
        f"Logs path: {status.logs_path}",
        f"Artifacts path: {status.artifacts_path}",
        f"Session file path: {status.session_file_path}",
        f"Server log path: {status.server_log_path}",
        f"Tunnel log path: {status.tunnel_log_path}",
        f"Last refreshed at: {refreshed_at}",
    ]
    return "\n".join(lines)


def current_timestamp_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def open_in_file_manager(target: str | Path) -> None:
    resolved_target = Path(target).expanduser().resolve()
    if not resolved_target.exists():
        raise FileNotFoundError(f"{resolved_target} does not exist")

    if sys.platform.startswith("darwin"):
        subprocess.Popen(["open", str(resolved_target)])
        return
    if os.name == "nt":
        os.startfile(str(resolved_target))  # type: ignore[attr-defined]
        return

    subprocess.Popen(["xdg-open", str(resolved_target)])


def _as_text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _as_pid_text(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return UNAVAILABLE_TEXT
