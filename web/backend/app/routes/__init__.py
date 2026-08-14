"""HTTP route modules.

Routes stay thin: they validate input, call a service, and shape the response. Business logic lives
in ``app/services/``. The WebSocket lives in ``app/transport/`` rather than here, because a push
channel with a replay contract is a different thing from a request/response endpoint.
"""

from fastapi import APIRouter

from . import config, health, pages, session, transcript


def build_router() -> APIRouter:
    """Aggregate every route module into a single router for the app factory."""
    router = APIRouter()
    router.include_router(health.router)
    router.include_router(session.router)
    router.include_router(transcript.router)
    router.include_router(config.router)
    # Pages last: their catch-all-ish paths must not shadow an API route.
    router.include_router(pages.router)
    return router


__all__ = ["build_router"]
