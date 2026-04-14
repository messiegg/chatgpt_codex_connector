from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass
from pathlib import Path
from time import sleep

from bridge_server.config import BridgeSettings
from storage import JobRecord, JobRepository, JobStatus
from worker.codex_runner import run_codex


logger = logging.getLogger(__name__)

SUMMARY_MAX_CHARS = 2000
SUMMARY_MAX_LINES = 20


@dataclass(slots=True)
class FailureResult:
    error_message: str
    stderr: str
    summary: str
    command: str | None = None
    return_code: int | None = None
    duration_seconds: float = 0.0


def _trim_text(value: str, *, max_lines: int = SUMMARY_MAX_LINES, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    if not value:
        return ""
    lines = value.strip().splitlines()
    limited = "\n".join(lines[:max_lines]).strip()
    if len(limited) > max_chars:
        return limited[:max_chars].rstrip()
    return limited


def build_summary(*, stdout: str, stderr: str, return_code: int) -> str:
    if return_code == 0:
        excerpt = _trim_text(stdout)
        if excerpt:
            return excerpt
        return "command succeeded but stdout is empty"

    stderr_excerpt = _trim_text(stderr)
    if stderr_excerpt:
        return f"command failed with return code {return_code}\n\n{stderr_excerpt}"
    return f"command failed with return code {return_code}"


class JobPoller:
    def __init__(self, settings: BridgeSettings, repository: JobRepository) -> None:
        self.settings = settings
        self.repository = repository
        self._should_stop = False

    def request_stop(self) -> None:
        self._should_stop = True

    def run_forever(self) -> None:
        self.settings.ensure_runtime_dirs()
        self.repository.database.initialize()
        while not self._should_stop:
            processed = self.run_once()
            if not processed:
                sleep(self.settings.worker_poll_interval_seconds)

    def run_once(self) -> bool:
        job = self.repository.claim_next_queued_job()
        if job is None:
            return False

        logger.info("claimed job %s", job.job_id)
        self._process_job(job)
        return True

    def _process_job(self, job: JobRecord) -> None:
        artifact_dir = Path(job.artifact_dir).expanduser().resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._write_text_file(artifact_dir / "prompt.txt", job.prompt)

            if not self._is_work_dir_allowed(job.work_dir):
                error_message = "work_dir is outside the allowed demo workspace"
                failure = FailureResult(
                    error_message=error_message,
                    stderr=error_message,
                    summary=error_message,
                )
                self._finalize_failure(job, artifact_dir, failure)
                return

            result = run_codex(
                codex_command=self.settings.codex_command,
                prompt=job.prompt,
                work_dir=job.work_dir,
            )
            summary = build_summary(
                stdout=result.stdout,
                stderr=result.stderr,
                return_code=result.return_code,
            )
            self._write_execution_files(
                artifact_dir=artifact_dir,
                stdout=result.stdout,
                stderr=result.stderr,
                summary=summary,
            )

            status = JobStatus.SUCCEEDED if result.return_code == 0 else JobStatus.FAILED
            error_message = None
            if status is JobStatus.FAILED:
                error_message = _trim_text(result.stderr) or f"command failed with return code {result.return_code}"

            updated_job = self.repository.update_job_result(
                job.job_id,
                status=status,
                return_code=result.return_code,
                error_message=error_message,
                summary=summary,
                command=result.command,
            )
            if updated_job is None:
                raise RuntimeError(f"job disappeared while updating result: {job.job_id}")

            self._write_metadata(
                artifact_dir=artifact_dir,
                job=updated_job,
                duration_seconds=result.duration_seconds,
            )
            logger.info("completed job %s with status=%s", job.job_id, updated_job.status.value)
        except KeyboardInterrupt:
            logger.warning("job %s interrupted by keyboard interrupt", job.job_id)
            failure = FailureResult(
                error_message="worker interrupted by keyboard interrupt",
                stderr="worker interrupted by keyboard interrupt",
                summary="worker interrupted by keyboard interrupt",
            )
            self._finalize_failure(job, artifact_dir, failure)
            raise
        except Exception as exc:
            logger.exception("job %s failed inside worker", job.job_id)
            stderr_text = traceback.format_exc()
            failure = FailureResult(
                error_message=str(exc),
                stderr=stderr_text,
                summary=f"worker failed before completing the command: {exc}",
            )
            self._finalize_failure(job, artifact_dir, failure)

    def _finalize_failure(self, job: JobRecord, artifact_dir: Path, failure: FailureResult) -> None:
        try:
            self._write_execution_files(
                artifact_dir=artifact_dir,
                stdout="",
                stderr=failure.stderr,
                summary=failure.summary,
            )
        except Exception:
            logger.exception("failed to write failure artifacts for job %s", job.job_id)

        updated_job = self.repository.update_job_result(
            job.job_id,
            status=JobStatus.FAILED,
            return_code=failure.return_code,
            error_message=failure.error_message,
            summary=failure.summary,
            command=failure.command,
        )
        if updated_job is None:
            logger.error("failed to update job %s to failed state", job.job_id)
            return

        try:
            self._write_metadata(
                artifact_dir=artifact_dir,
                job=updated_job,
                duration_seconds=failure.duration_seconds,
            )
        except Exception:
            logger.exception("failed to write metadata for job %s", job.job_id)
            return

        logger.info("completed job %s with status=%s", job.job_id, updated_job.status.value)

    def _is_work_dir_allowed(self, work_dir: str | Path) -> bool:
        resolved_work_dir = Path(work_dir).expanduser().resolve()
        allowed_root = self.settings.allowed_work_root.expanduser().resolve()
        return resolved_work_dir.is_relative_to(allowed_root)

    def _write_execution_files(self, *, artifact_dir: Path, stdout: str, stderr: str, summary: str) -> None:
        self._write_text_file(artifact_dir / "stdout.log", stdout)
        self._write_text_file(artifact_dir / "stderr.log", stderr)
        self._write_text_file(artifact_dir / "summary.txt", summary)

    def _write_metadata(self, *, artifact_dir: Path, job: JobRecord, duration_seconds: float) -> None:
        metadata = {
            "job_id": job.job_id,
            "status": job.status.value,
            "command": job.command,
            "work_dir": job.work_dir,
            "return_code": job.return_code,
            "duration_seconds": round(duration_seconds, 3),
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
        metadata_path = artifact_dir / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_text_file(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
