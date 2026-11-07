"""A recording's title: naming the untitled ones on approval, and renaming any of them (D-084).

Titles are written automatically once a transcript is finished (``services/transcript/titles.py``).
These routes are the two things that are the owner's to decide: whether to name the recordings made
before that existed, and what any one of them should be called instead.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..services.transcript import archive
from ..services.transcript.store import TranscriptStore
from .sessions import _running_key

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class Rename(BaseModel):
    title: str = Field(..., max_length=120)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "severity": "warning"}},
    )


@router.get("/untitled/plan")
async def untitled_plan(request: Request) -> dict[str, Any]:
    """How many finished recordings have no title, and which model would name them. Starts
    nothing — naming them is the owner's call."""
    return await run_in_threadpool(request.app.state.titles.plan)


@router.post("/untitled/name")
async def name_untitled(request: Request) -> dict[str, Any]:
    """Name every untitled recording, one at a time, in the background.

    Progress arrives as ``titles.progress`` and ``titles.done``, and each title as
    ``session.titled``.
    """
    titles = request.app.state.titles
    if not titles.model():
        raise _error(409, "no-model", "No language model is set up. Choose one in Settings.")
    return titles.start_backfill()


@router.patch("/{key}")
async def rename_session(request: Request, key: str, body: Rename) -> dict[str, Any]:
    """Give a recording the title the owner chose. Replaces whatever it had."""
    title = " ".join(body.title.split())
    if not title:
        raise _error(422, "empty-title", "A title needs at least one word.")
    if key == _running_key(getattr(request.app.state, "session_manager", None)):
        raise _error(409, "still-recording", "Rename it once the recording has stopped.")
    path = archive.find(request.app.state.config.resolve(), key)
    if path is None:
        raise _error(404, "unknown-session", "There is no session with that key.")

    def write() -> bool:
        with TranscriptStore(path) as store:
            return store.metadata() is not None and store.set_title(title)

    if not await run_in_threadpool(write):
        raise _error(409, "no-metadata", "That session has no record to put a title on.")
    request.app.state.hub.emit("session.titled", {"key": key, "title": title})
    return {"key": key, "title": title}
