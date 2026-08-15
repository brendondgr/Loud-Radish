"""The window capture's preview frame and its state (D-022).

**The preview is pulled as an image, never pushed over the WebSocket.** The socket's backpressure
policy makes transcript events critical and undroppable, and video frames sharing that channel are
the one thing capable of delaying a committed segment. A still image on a timer costs a request a
second and cannot interfere with anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/capture", tags=["capture"])


def _manager(request: Request):  # noqa: ANN201 - returns SessionManager
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail={"error": {"code": "not-ready"}})
    return manager


@router.get("/state")
async def capture_state(request: Request) -> dict[str, Any]:
    """What the monitor pane draws. Reports `recording: false` when nothing is capturing."""
    manager = _manager(request)
    return manager.capture_state()


@router.get("/preview.jpg")
async def preview(request: Request) -> FileResponse:
    """The most recent preview frame.

    404 whenever there is not one — no capture, no `jpegenc`, or the first frame not yet written.
    The monitor treats that as its documented degraded state and shows a static card, because a
    black rectangle and a broken preview look identical (D-020).
    """
    manager = _manager(request)
    recorder = getattr(manager, "_recorder", None)
    path = Path(recorder.state.preview_path) if recorder and recorder.state.preview_path else None

    if path is None or not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "no-preview",
                    "message": "No preview frame is available.",
                    "severity": "info",
                }
            },
        )

    return FileResponse(
        path,
        media_type="image/jpeg",
        # The file is rewritten in place roughly once a second, so any caching at all serves a
        # stale frame — which reads as a capture that has frozen.
        headers={"Cache-Control": "no-store, max-age=0"},
    )
