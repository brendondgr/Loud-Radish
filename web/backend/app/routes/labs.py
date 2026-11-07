"""The labs: prototype pages for trying out a redesign before it replaces the real one (D-082).

``/labs/sessions/`` is a card-based Recordings page with a panel of layout options and a way to
export the user's preferences as text; ``/labs/main/`` draws the main screen in the style chosen
there. Everything it reads comes from the ordinary sessions API, including the
media routes that started here and moved to ``session_media`` (redesign Step 2); their old
``/api/labs/`` paths stay as aliases until the labs are retired.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import session_media
from .pages import _templates

router = APIRouter(tags=["labs"])

# -- the page ---------------------------------------------------------------------------------


@router.get("/labs/sessions", include_in_schema=False)
async def labs_sessions_redirect() -> RedirectResponse:
    return RedirectResponse("/labs/sessions/", status_code=307)


@router.get("/labs/sessions/", response_class=HTMLResponse)
async def labs_sessions_page(request: Request) -> HTMLResponse:
    """The card-based recordings prototype."""
    return _templates(request).TemplateResponse(request, "pages/labs/sessions.html", {})


@router.get("/labs/main", include_in_schema=False)
async def labs_main_redirect() -> RedirectResponse:
    return RedirectResponse("/labs/main/", status_code=307)


@router.get("/labs/main/", response_class=HTMLResponse)
async def labs_main_page(request: Request) -> HTMLResponse:
    """The main screen redrawn in the recordings lab's style. Look-only."""
    return _templates(request).TemplateResponse(request, "pages/labs/main.html", {})


# -- the lab's old API paths ------------------------------------------------------------------
#
# The routes now live in ``session_media`` under ``/api/sessions/`` (redesign Step 2). These aliases
# keep the labs working until they are retired (Step 9).

router.add_api_route(
    "/api/labs/sessions/glance", session_media.glances, methods=["GET"], include_in_schema=False
)
router.add_api_route(
    "/api/labs/sessions/{key}/thumbnail",
    session_media.thumbnail,
    methods=["GET"],
    include_in_schema=False,
)
router.add_api_route(
    "/api/labs/sessions/{key}/file/{kind}",
    session_media.download_file,
    methods=["GET"],
    include_in_schema=False,
)
router.add_api_route(
    "/api/labs/sessions/{key}/reveal",
    session_media.reveal_folder,
    methods=["POST"],
    include_in_schema=False,
)
