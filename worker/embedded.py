from __future__ import annotations

import logging
from threading import Thread

from bridge_server.config import BridgeSettings
from storage import JobRepository, SQLiteDatabase
from worker.poller import JobPoller


logger = logging.getLogger(__name__)

EMBEDDED_WORKER_JOIN_TIMEOUT_SECONDS = 5.0


class EmbeddedWorkerHandle:
    def __init__(self, settings: BridgeSettings) -> None:
        self._settings = settings
        self._poller: JobPoller | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return

        database = SQLiteDatabase(self._settings.database_path)
        repository = JobRepository(database)
        self._poller = JobPoller(self._settings, repository)
        self._thread = Thread(
            target=self._run,
            name="bridge-embedded-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info("started embedded worker thread")

    def stop(self) -> None:
        poller = self._poller
        thread = self._thread
        if poller is None or thread is None:
            return

        poller.request_stop()
        thread.join(timeout=EMBEDDED_WORKER_JOIN_TIMEOUT_SECONDS)
        if thread.is_alive():
            logger.warning("embedded worker did not stop within %.1f seconds", EMBEDDED_WORKER_JOIN_TIMEOUT_SECONDS)
        else:
            logger.info("embedded worker stopped")

        self._poller = None
        self._thread = None

    def _run(self) -> None:
        poller = self._poller
        if poller is None:
            return

        try:
            poller.run_forever()
        except Exception:
            logger.exception("embedded worker crashed")
