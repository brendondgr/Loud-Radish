"""Server-rendered pages.

The frontend is Jinja2 templates plus plain CSS and ES modules, served from this process — no build
step and no second toolchain (Decision D-011). These routes do nothing but render; every piece of
data arrives over the API or the WebSocket, so a page is the same whether it is a first load or a
reload mid-session.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["pages"])


def _templates(request: Request):  # noqa: ANN202 - returns Jinja2Templates
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "no-templates",
                    "message": (
                        "The frontend templates are missing. Expected them under "
                        "web/frontend/templates."
                    ),
                    "severity": "critical",
                }
            },
        )
    return templates


@router.get("/", response_class=HTMLResponse)
async def app_page(request: Request) -> HTMLResponse:
    """The live application: transcript pane, chat pane, header, status bar."""
    return _templates(request).TemplateResponse(request, "pages/app.html", {})


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request) -> HTMLResponse:
    """Past sessions, with export. Everything it shows arrives over ``/api/sessions``."""
    return _templates(request).TemplateResponse(request, "pages/sessions.html", {})
