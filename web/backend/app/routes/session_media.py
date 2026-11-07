"""What a recording card needs beyond the listing: a picture, files, its folder, a glance (D-082).

Promoted from the recordings lab (``routes/labs.py``) for the redesigned Recordings page and the
main screen's details column:

- a picture of the recording — the stored ``preview.jpg``, or a frame of the video by the second;
- the video or the audio as a download with a proper name;
- opening the recording's folder in the system file manager;
- a *glance* — opening words, first summary, keywords — standing in until a title is written.

Nothing here writes to a recording. Keys are resolved to paths on the server, as everywhere else,
so no route takes a path from the request. Registered before ``sessions`` so that
``/api/sessions/glance`` is not read as a session called "glance".
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from ..services.export.webapp import video_type as video_mime
from ..services.recording import resolve_recording
from ..services.recording.frames import frame_jpeg
from ..services.transcript import archive
from ..services.transcript.glance import glance

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

#: Thumbnails change only when the file does, and the URL names the moment, so a browser may keep
#: them for an hour.
_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600"}

#: How many glances one request may ask for: about two screens of cards.
GLANCE_BATCH = 60


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


@router.get("/glance")
async def glances(request: Request, keys: str = Query(..., max_length=4000)) -> dict[str, Any]:
    """What each named recording is about: its opening words, first summary and keywords.

    Asked for in batches as cards scroll into view, rather than for all of them at once.
    """
    config = _config(request)
    wanted = [key for key in keys.split(",") if key][:GLANCE_BATCH]

    def read() -> dict[str, Any]:
        found: dict[str, Any] = {}
        for key in wanted:
            path = archive.find(config, key)
            if path is not None:
                found[key] = glance(path).as_dict()
        return found

    return {"glances": await run_in_threadpool(read)}


@router.get("/{key}/thumbnail")
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


@router.get("/{key}/file/{kind}")
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


@router.post("/{key}/reveal")
async def reveal_folder(request: Request, key: str) -> dict[str, Any]:
    """Open the recording's folder in the system file manager.

    Returns the path either way, so the page can offer to copy it when no file manager answers.
    """
    directory = _layout(request, key).directory
    return {"path": str(directory), "opened": open_in_file_manager(directory)}


def open_in_file_manager(directory: Path) -> bool:
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
