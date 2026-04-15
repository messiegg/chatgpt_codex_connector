from __future__ import annotations

import logging
import signal
from contextlib import contextmanager
from types import FrameType

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from bridge_server.config import BridgeSettings
from bridge_server.service import JobService
from mcp_server.result_widget import register_result_widget_resource
from mcp_server.tools import register_tools
from storage import JobRepository, SQLiteDatabase
from worker.embedded import EmbeddedWorkerHandle


logger = logging.getLogger(__name__)

SERVER_NAME = "Codex Bridge MCP"
SERVER_INSTRUCTIONS = (
    "This remote MCP server exposes bridge tools for creating jobs, checking job "
    "state, listing jobs, and reading text artifacts. It is a no-auth local "
    "development adapter backed by the existing SQLite job system and local worker."
)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def build_service(settings: BridgeSettings) -> JobService:
    database = SQLiteDatabase(settings.database_path)
    repository = JobRepository(database)
    return JobService(settings, repository)


@contextmanager
def embedded_worker_runtime(settings: BridgeSettings):
    worker: EmbeddedWorkerHandle | None = None
    if settings.embed_worker:
        worker = EmbeddedWorkerHandle(settings)
        worker.start()

    try:
        yield
    finally:
        if worker is not None:
            worker.stop()


@contextmanager
def shutdown_signal_handlers():
    def handle_shutdown(signum: int, frame: FrameType | None) -> None:
        del frame
        raise KeyboardInterrupt(f"received signal {signum}")

    previous_handlers: dict[int, signal.Handlers] = {}
    for signum in (signal.SIGTERM,):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, handle_shutdown)

    try:
        yield
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def create_mcp_server(settings: BridgeSettings | None = None) -> FastMCP:
    resolved_settings = settings or BridgeSettings.from_env()
    service = build_service(resolved_settings)
    service.initialize()

    mcp = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        host=resolved_settings.mcp_host,
        port=resolved_settings.mcp_port,
        streamable_http_path=resolved_settings.mcp_path,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
        json_response=True,
        log_level="INFO",
    )

    register_result_widget_resource(mcp)
    register_tools(mcp, service)

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health_check(request: Request) -> JSONResponse:
        del request
        return JSONResponse(service.health().model_dump())

    logger.info(
        "configured MCP server host=%s port=%s path=%s",
        resolved_settings.mcp_host,
        resolved_settings.mcp_port,
        resolved_settings.mcp_path,
    )
    return mcp


def main() -> None:
    configure_logging()
    settings = BridgeSettings.from_env()
    server = create_mcp_server(settings)
    try:
        with shutdown_signal_handlers():
            with embedded_worker_runtime(settings):
                server.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("MCP server interrupted, shutting down")


if __name__ == "__main__":
    main()
