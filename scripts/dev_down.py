from __future__ import annotations

import json
import os
from pathlib import Path

from dev_common import PROJECT_ROOT, load_dotenv, process_is_alive, terminate_process, terminate_process_group


def _session_path() -> Path:
    default_path = PROJECT_ROOT / "logs" / "dev_session.json"
    load_dotenv(PROJECT_ROOT / ".env")

    configured_logs_dir = None
    for env_name in ("CODEX_BRIDGE_LOGS_DIR", "BRIDGE_LOGS_DIR"):
        value = os.getenv(env_name)
        if value:
            configured_logs_dir = Path(value).expanduser().resolve()
            break

    if configured_logs_dir is None:
        return default_path
    return configured_logs_dir / "dev_session.json"


def main() -> int:
    session_path = _session_path()
    if not session_path.is_file():
        return 0

    try:
        session = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    supervisor_pid = session.get("supervisor_pid")
    server_pid = session.get("mcp_server_pid")
    tunnel_pid = session.get("tunnel_pid")

    if isinstance(supervisor_pid, int):
        terminate_process(supervisor_pid)

    if isinstance(server_pid, int) and process_is_alive(server_pid):
        terminate_process_group(server_pid)

    if isinstance(tunnel_pid, int) and process_is_alive(tunnel_pid):
        terminate_process_group(tunnel_pid)

    if session_path.exists():
        try:
            session_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
