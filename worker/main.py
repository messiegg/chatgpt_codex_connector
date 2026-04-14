from __future__ import annotations

import logging

from bridge_server.config import BridgeSettings
from storage import JobRepository, SQLiteDatabase
from worker.poller import JobPoller


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    configure_logging()
    settings = BridgeSettings.from_env()
    database = SQLiteDatabase(settings.database_path)
    repository = JobRepository(database)
    poller = JobPoller(settings, repository)

    try:
        poller.run_forever()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("worker interrupted, shutting down")
        poller.request_stop()
