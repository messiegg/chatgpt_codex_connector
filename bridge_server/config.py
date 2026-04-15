from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


WORK_DIR_OUTSIDE_ALLOWED_ROOTS_MESSAGE = "work_dir is outside the allowed work roots"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _env_raw_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return None


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _env_path(default: Path, *names: str) -> Path:
    value = _env_value(*names)
    if not value:
        return default.resolve()
    return Path(value).expanduser().resolve()


def _resolve_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _parse_allowed_work_roots(
    default: Path,
    *,
    roots_env_names: Sequence[str],
    root_env_names: Sequence[str],
) -> tuple[Path, ...]:
    raw_value = _env_raw_value(*roots_env_names)
    if raw_value is None or raw_value == "":
        return (_env_path(default, *root_env_names),)

    stripped_value = raw_value.strip()
    if stripped_value.startswith("["):
        try:
            parsed_value = json.loads(stripped_value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON array for {', '.join(roots_env_names)}"
            ) from exc

        if not isinstance(parsed_value, list) or any(not isinstance(item, str) for item in parsed_value):
            raise ValueError(f"{', '.join(roots_env_names)} must be a JSON array of strings")
        raw_items = parsed_value
    else:
        raw_items = re.split(r"[\n,]", stripped_value)

    resolved_roots: list[Path] = []
    seen_roots: set[Path] = set()
    for raw_item in raw_items:
        normalized_item = raw_item.strip()
        if not normalized_item:
            continue

        resolved_root = _resolve_path(normalized_item)
        if resolved_root in seen_roots:
            continue

        seen_roots.add(resolved_root)
        resolved_roots.append(resolved_root)

    if not resolved_roots:
        raise ValueError("allowed_work_roots must contain at least one valid path")

    return tuple(resolved_roots)


def _env_str(default: str, *names: str) -> str:
    value = _env_value(*names)
    if value is None:
        return default
    return value


def _env_int(default: int, *names: str) -> int:
    value = _env_value(*names)
    if value is None:
        return default
    return int(value)


def _env_float(default: float, *names: str) -> float:
    value = _env_value(*names)
    if value is None:
        return default
    return float(value)


def _env_bool(default: bool, *names: str) -> bool:
    value = _env_value(*names)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value '{value}' for {', '.join(names)}")


def is_path_within_allowed_roots(candidate: Path, allowed_roots: Sequence[Path]) -> bool:
    resolved_candidate = _resolve_path(candidate)
    for root in allowed_roots:
        if resolved_candidate == root or resolved_candidate.is_relative_to(root):
            return True
    return False


@dataclass(slots=True, frozen=True)
class BridgeSettings:
    database_path: Path
    artifacts_dir: Path
    logs_dir: Path
    codex_command: str
    worker_poll_interval_seconds: float
    default_work_dir: Path
    allowed_work_roots: tuple[Path, ...]
    host: str
    port: int
    mcp_host: str
    mcp_port: int
    mcp_path: str
    embed_worker: bool

    def __post_init__(self) -> None:
        if not self.allowed_work_roots:
            raise ValueError("allowed_work_roots must not be empty")

        for root in self.allowed_work_roots:
            if not root.is_absolute() or root != _resolve_path(root):
                raise ValueError("allowed_work_roots must contain resolved absolute paths")

        if not is_path_within_allowed_roots(self.default_work_dir, self.allowed_work_roots):
            raise ValueError("default_work_dir is outside the allowed work roots")

    @property
    def allowed_work_root(self) -> Path:
        return self.allowed_work_roots[0]

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        root = _project_root()
        mcp_path = _env_str(
            "/mcp",
            "CODEX_BRIDGE_MCP_PATH",
            "BRIDGE_MCP_PATH",
        )
        if not mcp_path.startswith("/"):
            mcp_path = f"/{mcp_path}"

        return cls(
            database_path=_env_path(
                root / "data" / "bridge.db",
                "CODEX_BRIDGE_DATABASE_PATH",
                "BRIDGE_DATABASE_PATH",
            ),
            artifacts_dir=_env_path(
                root / "artifacts",
                "CODEX_BRIDGE_ARTIFACTS_DIR",
                "BRIDGE_ARTIFACTS_DIR",
            ),
            logs_dir=_env_path(
                root / "logs",
                "CODEX_BRIDGE_LOGS_DIR",
                "BRIDGE_LOGS_DIR",
            ),
            codex_command=_env_str(
                "codex",
                "CODEX_BRIDGE_CODEX_COMMAND",
                "BRIDGE_CODEX_COMMAND",
            ),
            worker_poll_interval_seconds=_env_float(
                2.0,
                "CODEX_BRIDGE_WORKER_POLL_INTERVAL_SECONDS",
                "BRIDGE_WORKER_POLL_INTERVAL_SECONDS",
            ),
            default_work_dir=_env_path(
                root / "data" / "demo_workspace",
                "CODEX_BRIDGE_DEFAULT_WORK_DIR",
                "BRIDGE_DEFAULT_WORK_DIR",
            ),
            allowed_work_roots=_parse_allowed_work_roots(
                root / "data" / "demo_workspace",
                roots_env_names=(
                    "CODEX_BRIDGE_ALLOWED_WORK_ROOTS",
                    "BRIDGE_ALLOWED_WORK_ROOTS",
                ),
                root_env_names=(
                    "CODEX_BRIDGE_ALLOWED_WORK_ROOT",
                    "BRIDGE_ALLOWED_WORK_ROOT",
                ),
            ),
            host=_env_str(
                "127.0.0.1",
                "CODEX_BRIDGE_HOST",
                "BRIDGE_HOST",
            ),
            port=_env_int(
                8000,
                "CODEX_BRIDGE_PORT",
                "BRIDGE_PORT",
            ),
            mcp_host=_env_str(
                "127.0.0.1",
                "CODEX_BRIDGE_MCP_HOST",
                "BRIDGE_MCP_HOST",
            ),
            mcp_port=_env_int(
                8001,
                "CODEX_BRIDGE_MCP_PORT",
                "BRIDGE_MCP_PORT",
            ),
            mcp_path=mcp_path,
            embed_worker=_env_bool(
                False,
                "CODEX_BRIDGE_EMBED_WORKER",
                "BRIDGE_EMBED_WORKER",
            ),
        )

    def ensure_runtime_dirs(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.default_work_dir.mkdir(parents=True, exist_ok=True)
        for allowed_work_root in self.allowed_work_roots:
            allowed_work_root.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "database_path",
            "artifacts_dir",
            "logs_dir",
            "default_work_dir",
        ):
            payload[key] = str(payload[key])
        payload["allowed_work_roots"] = [str(path) for path in self.allowed_work_roots]
        payload["allowed_work_root"] = str(self.allowed_work_root)
        return payload
