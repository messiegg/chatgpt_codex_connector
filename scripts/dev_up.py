from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from urllib import error, request

from dev_common import PROJECT_ROOT, configure_logging, load_dotenv

from bridge_server.config import BridgeSettings


logger = logging.getLogger(__name__)

SESSION_FILENAME = "dev_session.json"
SERVER_LOG_FILENAME = "dev_mcp_server.log"
TUNNEL_LOG_FILENAME = "dev_tunnel.log"
HEALTHCHECK_TIMEOUT_SECONDS = 20.0
TUNNEL_DISCOVERY_INITIAL_TIMEOUT_SECONDS = 15.0
TUNNEL_DISCOVERY_REFRESH_INTERVAL_SECONDS = 2.0
SUPERVISOR_POLL_INTERVAL_SECONDS = 0.2
PROCESS_SHUTDOWN_TIMEOUT_SECONDS = 5.0


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _ensure_embed_worker_default() -> None:
    if _env_value("CODEX_BRIDGE_EMBED_WORKER", "BRIDGE_EMBED_WORKER") is None:
        os.environ["BRIDGE_EMBED_WORKER"] = "true"


def _health_url(settings: BridgeSettings) -> str:
    return f"http://{settings.mcp_host}:{settings.mcp_port}/health"


def _local_mcp_url(settings: BridgeSettings) -> str:
    return f"http://{settings.mcp_host}:{settings.mcp_port}{settings.mcp_path}"


def _public_mcp_url(settings: BridgeSettings, public_base_url: str | None) -> str | None:
    if not public_base_url:
        return None
    return f"{public_base_url.rstrip('/')}{settings.mcp_path}"


def _http_get_json(url: str, *, timeout_seconds: float = 2.0) -> dict[str, object] | None:
    try:
        with request.urlopen(url, timeout=timeout_seconds) as response:
            payload = response.read().decode("utf-8")
    except (error.URLError, error.HTTPError, TimeoutError):
        return None

    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if isinstance(loaded, dict):
        return loaded
    return None


def _wait_for_healthcheck(
    url: str,
    server_process: subprocess.Popen[str],
    *,
    should_stop: Callable[[], bool],
) -> bool:
    deadline = time.monotonic() + HEALTHCHECK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if should_stop():
            return False

        if server_process.poll() is not None:
            raise RuntimeError(f"MCP server exited early with code {server_process.returncode}")

        payload = _http_get_json(url)
        if payload and payload.get("status") == "ok":
            return True
        time.sleep(0.5)

    raise RuntimeError(f"Timed out waiting for MCP healthcheck at {url}")


def _resolve_tunnel_command(settings: BridgeSettings) -> list[str] | None:
    configured = _env_value("CODEX_BRIDGE_TUNNEL_COMMAND", "BRIDGE_TUNNEL_COMMAND")
    if configured:
        return shlex.split(configured)

    if shutil.which("ngrok"):
        return ["ngrok", "http", str(settings.mcp_port)]

    return None


def _discover_ngrok_public_url(
    *,
    should_stop: Callable[[], bool],
    timeout_seconds: float,
) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    api_url = "http://127.0.0.1:4040/api/tunnels"

    while time.monotonic() < deadline:
        if should_stop():
            return None

        payload = _http_get_json(api_url, timeout_seconds=min(timeout_seconds, 2.0))
        tunnels = payload.get("tunnels") if payload else None
        if isinstance(tunnels, list):
            https_urls = [
                tunnel.get("public_url")
                for tunnel in tunnels
                if isinstance(tunnel, dict) and isinstance(tunnel.get("public_url"), str)
            ]
            for public_url in https_urls:
                if public_url.startswith("https://"):
                    return public_url
            if https_urls:
                return https_urls[0]
        time.sleep(0.5)

    return None


class DevSupervisor:
    def __init__(self, settings: BridgeSettings) -> None:
        self.settings = settings
        self.session_path = settings.logs_dir / SESSION_FILENAME
        self.server_log_path = settings.logs_dir / SERVER_LOG_FILENAME
        self.tunnel_log_path = settings.logs_dir / TUNNEL_LOG_FILENAME
        self.server_process: subprocess.Popen[str] | None = None
        self.tunnel_process: subprocess.Popen[str] | None = None
        self.public_base_url: str | None = None
        self._server_log_handle = None
        self._tunnel_log_handle = None
        self._shutdown_requested = Event()
        self._shutdown_reason: str | None = None
        self._cleanup_done = False
        self._tunnel_supports_auto_discovery = False
        self._last_tunnel_discovery_attempt_at = 0.0

    def run(self) -> int:
        self.settings.ensure_runtime_dirs()
        self.server_process = self._start_process(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "run_mcp_server.py")],
            self.server_log_path,
            label="MCP server",
        )
        healthcheck_ready = _wait_for_healthcheck(
            _health_url(self.settings),
            self.server_process,
            should_stop=self.should_stop,
        )
        if not healthcheck_ready or self.should_stop():
            return 0

        tunnel_command = _resolve_tunnel_command(self.settings)
        if tunnel_command is not None:
            self.tunnel_process = self._start_process(
                tunnel_command,
                self.tunnel_log_path,
                label="tunnel",
            )
            self._tunnel_supports_auto_discovery = Path(tunnel_command[0]).name == "ngrok"
            if self._tunnel_supports_auto_discovery:
                self.public_base_url = _discover_ngrok_public_url(
                    should_stop=self.should_stop,
                    timeout_seconds=TUNNEL_DISCOVERY_INITIAL_TIMEOUT_SECONDS,
                )
                self._last_tunnel_discovery_attempt_at = time.monotonic()
        else:
            logger.info("public tunnel not enabled")

        self._write_session_file()
        self._print_runtime_summary()
        return self._run_foreground_loop()

    def shutdown(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True

        self._stop_managed_processes()

        if self.session_path.exists():
            try:
                self.session_path.unlink()
            except FileNotFoundError:
                pass

        if self._server_log_handle is not None:
            self._server_log_handle.close()
            self._server_log_handle = None
        if self._tunnel_log_handle is not None:
            self._tunnel_log_handle.close()
            self._tunnel_log_handle = None

    @property
    def _server_pid(self) -> int | None:
        return self.server_process.pid if self.server_process is not None else None

    @property
    def _tunnel_pid(self) -> int | None:
        return self.tunnel_process.pid if self.tunnel_process is not None else None

    def should_stop(self) -> bool:
        return self._shutdown_requested.is_set()

    def request_shutdown(self, reason: str) -> None:
        if self._shutdown_requested.is_set():
            return

        self._shutdown_reason = reason
        self._shutdown_requested.set()
        logger.info("shutdown requested: %s", reason)

    def _start_process(
        self,
        command: list[str],
        log_path: Path,
        *,
        label: str,
    ) -> subprocess.Popen[str]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=os.environ.copy(),
            stdout=log_handle,
            stderr=log_handle,
            text=True,
            start_new_session=True,
        )
        if label == "MCP server":
            self._server_log_handle = log_handle
        else:
            self._tunnel_log_handle = log_handle

        logger.info("started %s pid=%s", label, process.pid)
        return process

    def _run_foreground_loop(self) -> int:
        while True:
            if self.should_stop():
                return 0

            server_process = self.server_process
            if server_process is not None:
                return_code = server_process.poll()
                if return_code is not None:
                    if return_code == 0:
                        logger.info("MCP server exited cleanly")
                    else:
                        logger.error("MCP server exited with code %s", return_code)
                    return return_code

            tunnel_process = self.tunnel_process
            if tunnel_process is not None:
                tunnel_return_code = tunnel_process.poll()
                if tunnel_return_code is not None:
                    logger.warning("tunnel exited with code %s", tunnel_return_code)
                    self.tunnel_process = None
                else:
                    self._refresh_public_base_url_if_needed()

            time.sleep(SUPERVISOR_POLL_INTERVAL_SECONDS)

    def _process_is_running(self, process: subprocess.Popen[str] | None) -> bool:
        return process is not None and process.poll() is None

    def _send_signal(self, process: subprocess.Popen[str] | None, signum: int) -> None:
        if process is None:
            return

        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            return
        except PermissionError:
            try:
                os.kill(process.pid, signum)
            except ProcessLookupError:
                return

    def _stop_managed_processes(self) -> None:
        managed_processes = {
            "MCP server": self.server_process,
            "tunnel": self.tunnel_process,
        }

        for process in managed_processes.values():
            self._send_signal(process, signal.SIGTERM)

        deadline = time.monotonic() + PROCESS_SHUTDOWN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not any(self._process_is_running(process) for process in managed_processes.values()):
                return
            time.sleep(SUPERVISOR_POLL_INTERVAL_SECONDS)

        for label, process in managed_processes.items():
            if self._process_is_running(process):
                logger.warning("%s did not stop after %.1f seconds; sending SIGKILL", label, PROCESS_SHUTDOWN_TIMEOUT_SECONDS)
                self._send_signal(process, signal.SIGKILL)

    def _write_session_file(self) -> None:
        payload = {
            "supervisor_pid": os.getpid(),
            "mcp_server_pid": self._server_pid,
            "tunnel_pid": self._tunnel_pid,
            "local_mcp_url": _local_mcp_url(self.settings),
            "public_mcp_url": _public_mcp_url(self.settings, self.public_base_url),
            "health_url": _health_url(self.settings),
            "server_log_path": str(self.server_log_path),
            "tunnel_log_path": str(self.tunnel_log_path),
            "artifacts_dir": str(self.settings.artifacts_dir),
        }
        self.session_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _refresh_public_base_url_if_needed(self) -> None:
        if not self._tunnel_supports_auto_discovery:
            return
        if self.public_base_url:
            return

        now = time.monotonic()
        if now - self._last_tunnel_discovery_attempt_at < TUNNEL_DISCOVERY_REFRESH_INTERVAL_SECONDS:
            return

        self._last_tunnel_discovery_attempt_at = now
        discovered_public_base_url = _discover_ngrok_public_url(
            should_stop=self.should_stop,
            timeout_seconds=0.5,
        )
        if not discovered_public_base_url:
            return

        self.public_base_url = discovered_public_base_url
        self._write_session_file()
        public_mcp_url = _public_mcp_url(self.settings, self.public_base_url)
        if public_mcp_url:
            print(f"public MCP URL discovered: {public_mcp_url}")
            print(f"ChatGPT Developer Mode address: {public_mcp_url}")

    def _print_runtime_summary(self) -> None:
        public_mcp_url = _public_mcp_url(self.settings, self.public_base_url)
        print(f"local health URL: {_health_url(self.settings)}")
        print(f"local MCP URL: {_local_mcp_url(self.settings)}")
        if public_mcp_url:
            print(f"public MCP URL: {public_mcp_url}")
            print(f"ChatGPT Developer Mode address: {public_mcp_url}")
        else:
            print("public MCP URL: unavailable")
            print("ChatGPT Developer Mode address: unavailable (enable a public tunnel)")
            if self.tunnel_process is None:
                print("tunnel: 未启用公网隧道")
            else:
                print(f"tunnel: started but public URL not discovered automatically; check {self.tunnel_log_path}")

        print(f"logs path: {self.settings.logs_dir}")
        print(f"artifacts path: {self.settings.artifacts_dir}")
        print(f"session file: {self.session_path}")
        print(f"embedded worker: {self.settings.embed_worker}")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    _ensure_embed_worker_default()
    configure_logging()
    settings = BridgeSettings.from_env()
    supervisor = DevSupervisor(settings)
    previous_handlers: dict[int, signal.Handlers] = {}

    def handle_shutdown(signum: int, frame: object) -> None:
        del frame
        supervisor.request_shutdown(signal.Signals(signum).name)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, handle_shutdown)

        return supervisor.run()
    except Exception as exc:
        logger.error("dev supervisor failed: %s", exc)
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        supervisor.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
