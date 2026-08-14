"""HTTP route modules.

Routes stay thin: they validate input, call a service, and shape the response. Business logic lives
in ``app/services/``. Modules are added here as each phase of
``docs/plans/live-seminar-transcriber.md`` lands.
"""

from fastapi import APIRouter

from . import health


def build_router() -> APIRouter:
    """Aggregate every route module into a single router for the app factory."""
    router = APIRouter()
    router.include_router(health.router)
    return router


__all__ = ["build_router"]
