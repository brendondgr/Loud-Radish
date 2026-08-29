"""Past sessions — listing, reading, exporting, and deleting.

The live application only ever knows about the session currently open. Once one stops, its
transcript lives in a SQLite file and every route under ``/api/transcript`` stops answering for it,
because those read the *running* session's store. That is correct for the live view and useless for
a talk that finished ten minutes ago, which is what these routes are for.

A session is addressed by its **key** — the file's stem — never by a path. A path from a request is
a path traversal waiting to be written, and there is no reason for the caller to supply one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..services.transcript import archive
from ..services.transcript import export as render_export
from ..services.transcript.store import TranscriptStore

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _config(request: Request):  # noqa: ANN202 - returns AppConfig
    return request.app.state.config.resolve()


def _open(request: Request, key: str) -> TranscriptStore:
    """Open a past session read-only, or 404 with a message that says what happened."""
    path = archive.find(_config(request), key)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "no-such-session",
                    "message": (
                        "That session is not in the session folder any more. "
                        "It may have been moved, deleted, or the folder changed in settings."
                    ),
                    "severity": "warning",
                }
            },
        )
    return TranscriptStore(path)


@router.get("")
async def list_sessions(request: Request) -> dict[str, Any]:
    """Every session on disk, newest first.

    Includes files that cannot be read, each with its reason. A session the user can see and cannot
    open is more useful than one that has silently vanished from the list.
    """
    config = _config(request)
    sessions = archive.list_sessions(config)
    running = getattr(request.app.state, "session_manager", None)
    return {
        "sessions": [session.as_dict() for session in sessions],
        "directory": str(archive.session_dir(config)),
        # So the page can mark the row that is still being written rather than presenting it as
        # finished with a duration that is about to change.
        "running_key": _running_key(running),
    }


def _running_key(manager: Any) -> str:
    store = getattr(manager, "store", None) if manager else None
    return getattr(store, "path", None).stem if store is not None else ""


@router.get("/{key}")
async def read_session(
    request: Request, key: str, limit: int = Query(2000, ge=1, le=20_000)
) -> dict[str, Any]:
    """One past session's transcript, summaries, and glossary."""
    store = _open(request, key)
    try:
        metadata = store.metadata()
        return {
            "key": key,
            "session": metadata.as_dict() if metadata else None,
            "stats": store.stats().as_dict(),
            # One transcription pass, not every row: a session holding both a live and a
            # post-capture pass would otherwise render the same talk twice on the page (D-022).
            "segments": [segment.as_event() for segment in store.latest_segments()[:limit]],
            "summaries": [summary.as_event() for summary in store.summaries()],
            "glossary": [term.as_event() for term in store.glossary()],
            "chat": [message.as_dict() for message in store.chat_history()],
        }
    finally:
        store.close()


@router.get("/{key}/export")
async def export_session(request: Request, key: str, fmt: str = Query("markdown")) -> Response:
    """Download a past session in any of the five formats."""
    store = _open(request, key)
    try:
        metadata = store.metadata()
        body, mime, extension = render_export(
            fmt,
            segments=store.latest_segments(),
            metadata=metadata,
            summaries=store.summaries(),
            glossary=store.glossary(),
            chat=store.chat_history(),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": {"code": "unknown-format", "message": str(exc)}}
        ) from exc
    finally:
        store.close()

    stem = metadata.started_at.strftime("%Y%m%d-%H%M") if metadata else key
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{stem}-transcript.{extension}"'},
    )


@router.delete("/{key}")
async def delete_session(request: Request, key: str) -> dict[str, Any]:
    """Delete a past session file.

    Refuses the session that is currently recording: deleting the file out from under an open
    connection loses the talk in progress, which is the one thing this application must not do.
    """
    manager = getattr(request.app.state, "session_manager", None)
    if key and key == _running_key(manager):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "session-running",
                    "message": "That session is still recording. Stop it before deleting it.",
                }
            },
        )

    path = archive.find(_config(request), key)
    if path is None:
        return {"deleted": False}
    path.unlink()
    return {"deleted": True}
