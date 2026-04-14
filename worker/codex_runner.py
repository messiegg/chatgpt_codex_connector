from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class CodexExecutionResult:
    stdout: str
    stderr: str
    return_code: int
    duration_seconds: float
    command: str
    work_dir: str
    started_at: str
    finished_at: str


def build_codex_command(codex_command: str, work_dir: str | Path) -> list[str]:
    command = shlex.split(codex_command)
    command.extend(
        [
            "exec",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--sandbox",
            "workspace-write",
            "-C",
            str(Path(work_dir).expanduser().resolve()),
            "-",
        ]
    )
    return command


def run_codex(*, codex_command: str, prompt: str, work_dir: str | Path) -> CodexExecutionResult:
    resolved_work_dir = Path(work_dir).expanduser().resolve()
    command_parts = build_codex_command(codex_command, resolved_work_dir)
    command_string = shlex.join(command_parts)
    started_at = utc_now_iso()
    started_perf = time.perf_counter()
    completed = subprocess.run(
        command_parts,
        input=prompt,
        capture_output=True,
        text=True,
        check=False,
    )
    finished_at = utc_now_iso()
    duration_seconds = time.perf_counter() - started_perf
    return CodexExecutionResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        return_code=completed.returncode,
        duration_seconds=duration_seconds,
        command=command_string,
        work_dir=str(resolved_work_dir),
        started_at=started_at,
        finished_at=finished_at,
    )
