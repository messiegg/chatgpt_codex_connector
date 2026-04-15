from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


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


@dataclass(slots=True, frozen=True)
class BridgeSettings:
    database_path: Path
    artifacts_dir: Path
    logs_dir: Path
    codex_command: str
    worker_poll_interval_seconds: float
    default_work_dir: Path
    allowed_work_root: Path
    host: str
    port: int
    mcp_host: str
    mcp_port: int
    mcp_path: str
    embed_worker: bool

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
            allowed_work_root=_env_path(
                root / "data" / "demo_workspace",
                "CODEX_BRIDGE_ALLOWED_WORK_ROOT",
                "BRIDGE_ALLOWED_WORK_ROOT",
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
        self.allowed_work_root.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "database_path",
            "artifacts_dir",
            "logs_dir",
            "default_work_dir",
            "allowed_work_root",
        ):
            payload[key] = str(payload[key])
        return payload
