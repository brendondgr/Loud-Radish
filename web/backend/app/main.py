"""FastAPI application factory.

The backend serves three things from one process: the JSON API, the WebSocket event stream, and the
server-rendered frontend. That is deliberate — the transport contract (BE §12) is already a web API,
so the frontend needs no bridging layer and no separate toolchain.

The server binds to loopback by default. This is a single-user local application with no
authentication model (see ``docs/architecture.md``); exposing it on a network interface would put an
unauthenticated transcript of a private room on that network.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import paths
from .config import ConfigStore, CredentialStore
from .routes import build_router
from .services.chat import ChatService
from .services.context import ContextWorker
from .services.export import ExportRegistry, ExportRunner
from .services.llm import build_llm
from .services.polish import PolishWorker
from .services.recording import migrate_flat_recordings
from .services.session import SessionManager
from .transport import EventHub, ws_router

logger = logging.getLogger(__name__)

APP_TITLE = "Live Seminar Transcriber"
APP_VERSION = "0.1.0"


def configure_logging() -> None:
    """Set up application logging.

    Transcript content is never logged (BE §18). Log records carry state and metrics, never speech.
    """
    level = os.environ.get("LOG_LEVEL", "info").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load configuration and bind the event loop; release everything on shutdown."""
    config: ConfigStore = app.state.config
    config.load()
    logger.info("Configuration resolved from %s", config.path)

    # Recordings made before the per-recording folder layout existed are grouped into it now, once.
    # Done at start-up rather than lazily because every reader — the listing, the past-sessions
    # page, the export — would otherwise need to understand both layouts forever.
    migrate_flat_recordings(config.resolve().recording.recording_dir)

    # The hub needs the running loop so the pipeline's worker threads can hand it events. This is
    # the only place asyncio and the audio path meet.
    app.state.hub.bind_loop(asyncio.get_running_loop())

    try:
        yield
    finally:
        chat = getattr(app.state, "chat", None)
        if chat is not None:
            await chat.shutdown()

        session = getattr(app.state, "session_manager", None)
        if session is not None:
            await session.shutdown()


def create_app(config: ConfigStore | None = None) -> FastAPI:
    """Build the application.

    Args:
        config: an existing store, so tests can supply an isolated config file rather than
            touching the developer's real one.
    """
    configure_logging()

    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        summary="Live seminar transcription with an LLM assistant over the running transcript.",
        lifespan=lifespan,
    )

    app.state.config = config or ConfigStore()
    app.state.credentials = CredentialStore()
    app.state.templates = _build_templates()

    # The hub is the seam between the pipeline's threads and the event loop: the manager emits by
    # calling `hub.emit` from whichever thread produced the event, and the hub marshals.
    app.state.hub = EventHub()
    app.state.session_manager = SessionManager(app.state.config, emit=app.state.hub.emit)

    # One export at a time, held here rather than on the session manager: an export runs against a
    # *past* session and outlives whatever is recording now, in the same way a post-capture pass
    # outlives the session that made its file.
    app.state.export_jobs = ExportRegistry()
    app.state.export_runner = ExportRunner(registry=app.state.export_jobs, emit=app.state.hub.emit)
    _wire_assistant(app)

    app.include_router(build_router())
    app.include_router(ws_router)
    _mount_static(app)

    return app


def _wire_assistant(app: FastAPI) -> None:
    """Attach chat orchestration and rolling summarisation to the session manager.

    Both are built from *providers* rather than from resolved objects, so that changing the model in
    settings takes effect on the next question rather than at the next restart. A backend captured
    once here would silently keep answering from whatever was configured at boot.
    """
    manager = app.state.session_manager

    def backend_factory():  # noqa: ANN202 - returns LlmBackend
        return build_llm(app.state.config.resolve().llm, app.state.credentials)

    app.state.chat = ChatService(
        store_provider=lambda: manager.store,
        config_provider=lambda: app.state.config.resolve(),
        backend_factory=backend_factory,
        emit=app.state.hub.emit,
        clock=lambda: manager.session_seconds,
    )

    manager.context_worker_factory = lambda store, config: ContextWorker(
        store=store,
        config=config,
        backend_factory=backend_factory,
        emit=app.state.hub.emit,
        clock=lambda: manager.session_seconds,
    )

    # The polish worker takes a config *provider* rather than a snapshot. Its settings — how often
    # to run, whether to run at all — are ones a user adjusts while listening to a talk, and a
    # snapshot would defer that until the next session.
    manager.polish_worker_factory = lambda store: PolishWorker(
        store=store,
        config_provider=lambda: app.state.config.resolve(),
        backend_factory=backend_factory,
        emit=app.state.hub.emit,
        silence_provider=lambda: manager.silence_seconds,
    )


def _build_templates() -> Jinja2Templates | None:
    """Bind the Jinja2 environment, or return ``None`` before the template tree exists."""
    if not paths.TEMPLATES_DIR.is_dir():
        logger.info(
            "No template directory at %s yet; page routes are unavailable", paths.TEMPLATES_DIR
        )
        return None
    templates = Jinja2Templates(directory=str(paths.TEMPLATES_DIR))
    templates.env.trim_blocks = True
    templates.env.lstrip_blocks = True
    return templates


class _NoCacheStatic(StaticFiles):
    """Static files served with caching disabled.

    Everything here is fetched over loopback from the same machine, so caching buys nothing
    measurable — while a stale stylesheet after an edit costs real time and is easy to mistake for
    a code bug. Correctness over a saving that does not exist.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:  # noqa: ANN001
        return False

    async def get_response(self, path: str, scope):  # noqa: ANN001, ANN201
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


def _mount_static(app: FastAPI) -> None:
    """Serve the frontend's CSS and JS, once that tree exists."""
    if not paths.STATIC_DIR.is_dir():
        logger.info("No static directory at %s yet; skipping mount", paths.STATIC_DIR)
        return
    app.mount("/static", _NoCacheStatic(directory=str(paths.STATIC_DIR)), name="static")


app = create_app()
