"""The labs: prototype pages for trying out a redesign before it replaces the real one (D-080).

``/labs/sessions/`` is a card-based Recordings page with a panel of layout options and a way to
export the user's preferences as text. Everything it reads comes from the ordinary sessions API;
the routes here are only what that API has no answer for yet — a picture of each recording, a file
download with a proper name, opening the folder, and a line saying what a recording is about.

Nothing here writes to a recording. Keys are resolved to paths on the server, as everywhere else,
so no route takes a path from the request.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from ..services.export.webapp import video_type as video_mime
from ..services.recording import resolve_recording
from ..services.recording.frames import frame_jpeg
from ..services.transcript import archive
from ..services.transcript.glance import glance
from .pages import _templates

router = APIRouter(tags=["labs"])

#: Thumbnails change only when the file does, and the URL names the moment, so a browser may keep
#: them for the length of a review session.
_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600"}


def _config(request: Request):  # noqa: ANN202 - returns AppConfig
    return request.app.state.config.resolve()


def _missing(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": code, "message": message, "severity": "warning"}},
    )


def _layout(request: Request, key: str):  # noqa: ANN202 - returns RecordingLayout
    layout = resolve_recording(archive.recording_dir(_config(request)), key)
    if layout is None or not layout.exists():
        raise _missing("no-recording", "There is no recording folder for that session.")
    return layout


# -- the page ---------------------------------------------------------------------------------


@router.get("/labs/sessions", include_in_schema=False)
async def labs_sessions_redirect() -> RedirectResponse:
    return RedirectResponse("/labs/sessions/", status_code=307)


@router.get("/labs/sessions/", response_class=HTMLResponse)
async def labs_sessions_page(request: Request) -> HTMLResponse:
    """The card-based recordings prototype."""
    return _templates(request).TemplateResponse(request, "pages/labs/sessions.html", {})


# -- what the sessions API does not say yet ---------------------------------------------------


@router.get("/api/labs/sessions/{key}/thumbnail")
async def thumbnail(
    request: Request,
    key: str,
    t: float | None = Query(None, ge=0),
    w: int = Query(480, ge=64, le=1280),
) -> Response:
    """A picture of the recording: the stored preview, or the frame at ``t`` seconds."""
    layout = _layout(request, key)
    if t is None and layout.preview.is_file():
        return FileResponse(layout.preview, media_type="image/jpeg", headers=_CACHE_HEADERS)

    video = layout.existing_video()
    if video is None:
        raise _missing("no-video", "That recording has no video to take a picture from.")
    body = await run_in_threadpool(frame_jpeg, video, t or 0.0, w)
    if body is None:
        raise _missing("no-frame", "No frame could be read from that video.")
    return Response(content=body, media_type="image/jpeg", headers=_CACHE_HEADERS)


@router.get("/api/labs/sessions/{key}/file/{kind}")
async def download_file(request: Request, key: str, kind: str) -> FileResponse:
    """The video or the audio, as a download named after the recording."""
    layout = _layout(request, key)
    if kind == "video":
        path = layout.existing_video()
        mime = video_mime(path) if path else None
    elif kind == "audio":
        path = layout.audio if layout.has_audio() else None
        mime = "audio/wav"
    else:
        raise _missing("unknown-kind", "Ask for the video or the audio.")
    if path is None:
        raise _missing(f"no-{kind}", f"That recording has no {kind} file.")
    return FileResponse(
        path,
        media_type=mime or "application/octet-stream",
        filename=f"{key}-{kind}{path.suffix}",
    )


@router.post("/api/labs/sessions/{key}/reveal")
async def reveal_folder(request: Request, key: str) -> dict[str, Any]:
    """Open the recording's folder in the system file manager.

    Returns the path either way, so the page can offer to copy it when no file manager answers.
    """
    directory = _layout(request, key).directory
    return {"path": str(directory), "opened": _open_in_file_manager(directory)}


def _open_in_file_manager(directory: Path) -> bool:
    if sys.platform.startswith("win"):
        command = ["explorer", str(directory)]
    elif sys.platform == "darwin":
        command = ["open", str(directory)]
    else:
        command = ["xdg-open", str(directory)]
    try:
        subprocess.Popen(  # noqa: S603 - fixed command, a path resolved from a validated key
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


@router.get("/api/labs/sessions/glance")
async def glances(request: Request, keys: str = Query(..., max_length=4000)) -> dict[str, Any]:
    """What each named recording is about: its opening words, first summary and keywords.

    Asked for in batches as cards scroll into view, rather than for all of them at once.
    """
    config = _config(request)
    wanted = [key for key in keys.split(",") if key][:60]

    def read() -> dict[str, Any]:
        found: dict[str, Any] = {}
        for key in wanted:
            path = archive.find(config, key)
            if path is not None:
                found[key] = glance(path).as_dict()
        return found

    return {"glances": await run_in_threadpool(read)}
