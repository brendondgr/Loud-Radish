"""Starting, finishing and cancelling a dictation.

Four routes and no streaming. A dictation is short and its progress is reported by the desktop's
own notifications and the tray, not by a page — the whole point is that nobody is looking at the
browser while it happens.

`toggle` is the one a keystroke calls, because a push-to-talk key is one key: press, speak, press.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..services.dictation import DictationError

router = APIRouter(prefix="/api/dictation", tags=["dictation"])


def _service(request: Request):  # noqa: ANN202
    service = getattr(request.app.state, "dictation", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Dictation is not available on this server.")
    return service


def _answer(state) -> dict[str, Any]:  # noqa: ANN001
    payload = state.as_dict()
    payload["summary"] = state.summary()
    return payload


def _attempt(action) -> dict[str, Any]:  # noqa: ANN001
    try:
        return _answer(action())
    except DictationError as exc:
        # 409, not 500: every one of these is a situation the user can resolve — a recording is
        # running, the model is not loaded, dictation is switched off.
        raise HTTPException(status_code=409, detail={"error": {"message": str(exc)}}) from exc


@router.get("")
async def get_dictation(request: Request) -> dict[str, Any]:
    """What the dictation is doing."""
    return _answer(_service(request).state())


@router.post("/toggle")
async def toggle_dictation(request: Request) -> dict[str, Any]:
    """Start one, or finish the one running. What the shortcut calls."""
    return _attempt(_service(request).toggle)


@router.post("/start")
async def start_dictation(request: Request) -> dict[str, Any]:
    return _attempt(_service(request).start)


@router.post("/stop")
async def stop_dictation(request: Request) -> dict[str, Any]:
    """Stop recording. The transcription and the paste happen afterwards, in the background."""
    return _attempt(_service(request).finish)


@router.post("/cancel")
async def cancel_dictation(request: Request) -> dict[str, Any]:
    """Throw it away. Nothing is transcribed and nothing is pasted."""
    return _attempt(_service(request).cancel)
