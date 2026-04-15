from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


logger = logging.getLogger(__name__)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue

        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def process_is_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_group(pid: int | None, *, timeout_seconds: float = 5.0) -> None:
    if not process_is_alive(pid):
        return

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        terminate_process(pid, timeout_seconds=timeout_seconds)
        return

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return
        time.sleep(0.1)

    if not process_is_alive(pid):
        return

    logger.warning("process group %s did not stop after %.1f seconds; sending SIGKILL", pid, timeout_seconds)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        terminate_process(pid, timeout_seconds=timeout_seconds)
        return


def terminate_process(pid: int | None, *, timeout_seconds: float = 5.0) -> None:
    if not process_is_alive(pid):
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return
        time.sleep(0.1)

    if not process_is_alive(pid):
        return

    logger.warning("process %s did not stop after %.1f seconds; sending SIGKILL", pid, timeout_seconds)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
