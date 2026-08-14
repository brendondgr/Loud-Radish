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

    # The hub needs the running loop so the pipeline's worker threads can hand it events. This is
    # the only place asyncio and the audio path meet.
    app.state.hub.bind_loop(asyncio.get_running_loop())

    try:
        yield
    finally:
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

    app.include_router(build_router())
    app.include_router(ws_router)
    _mount_static(app)

    return app


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


def _mount_static(app: FastAPI) -> None:
    """Serve the frontend's CSS and JS, once that tree exists."""
    if not paths.STATIC_DIR.is_dir():
        logger.info("No static directory at %s yet; skipping mount", paths.STATIC_DIR)
        return
    app.mount("/static", StaticFiles(directory=str(paths.STATIC_DIR)), name="static")


app = create_app()
