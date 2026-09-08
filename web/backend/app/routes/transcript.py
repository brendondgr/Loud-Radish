"""Transcript reading, search, and export.

The reconnection path lives here too: ``/api/transcript/since/{id}`` is the HTTP equivalent of the
WebSocket replay, for a client that has been away long enough that a frame burst is the wrong shape.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..schemas.api import (
    GlossaryResponse,
    PolishedBlocksResponse,
    SearchResponse,
    SegmentListResponse,
    SummariesResponse,
)
from ..services.transcript import export as render_export

router = APIRouter(prefix="/api/transcript", tags=["transcript"])


def _store(request: Request):  # noqa: ANN201 - returns TranscriptStore
    manager = getattr(request.app.state, "session_manager", None)
    store = manager.store if manager else None
    if store is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "no-session",
                    "message": "No session is open. Start recording to build a transcript.",
                    "severity": "info",
                }
            },
        )
    return store


@router.get("/revisions")
async def revisions(request: Request) -> dict[str, Any]:
    """Which transcription passes this session holds (D-022).

    Usually one. A window session that ran both a live pass and a post-capture one holds two, and
    the transcript pane offers a Live/Final switch only when it does — a switch between one thing
    and itself is chrome that explains nothing.
    """
    store = _store(request)
    available = store.revisions()
    return {
        "revisions": available,
        "latest": store.latest_revision(),
        # Named rather than numbered in the interface: "Final" means something to a reader and
        # "revision 1" does not.
        "labels": {"0": "Live", "1": "Final"},
    }


@router.get("/at/{revision}", response_model=SegmentListResponse)
async def at_revision(request: Request, revision: int) -> dict[str, Any]:
    """Every segment from one pass. What the Live/Final switch fetches."""
    store = _store(request)
    segments = store.segments_at(revision)
    return {
        "segments": [segment.as_event() for segment in segments],
        "last_id": store.last_segment_id(),
    }


@router.get("/since/{segment_id}", response_model=SegmentListResponse)
async def since(request: Request, segment_id: int, limit: int = Query(500, ge=1, le=5000)) -> dict:
    """Everything after ``segment_id``. Ordered by id, which makes the replay idempotent."""
    store = _store(request)
    segments = store.segments_since(segment_id, limit=limit)
    return {
        "segments": [segment.as_event() for segment in segments],
        "last_id": store.last_segment_id(),
    }


@router.get("/range", response_model=SegmentListResponse)
async def time_range(
    request: Request,
    start: float = Query(0.0, ge=0.0),
    end: float = Query(..., ge=0.0),
    revision: int | None = Query(None, ge=0),
) -> dict[str, Any]:
    """Everything of one pass overlapping a time range — what "summarise the last ten minutes"
    reads. The latest pass unless ``revision`` names another (D-065)."""
    store = _store(request)
    segments = store.segments_in_range(start, end, revision)
    return {
        "segments": [segment.as_event() for segment in segments],
        "last_id": store.last_segment_id(),
    }


@router.get("/search", response_model=SearchResponse)
async def search(
    request: Request,
    q: str = Query("", max_length=500),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Full-text search. A query that FTS5 cannot parse returns nothing rather than an error."""
    store = _store(request)
    return {"query": q, "segments": [s.as_event() for s in store.search(q, limit=limit)]}


def _optional_store(request: Request):  # noqa: ANN201 - returns TranscriptStore | None
    """The store if a session is open, otherwise ``None``.

    Used by the two endpoints the interface reads on *every* page load. "Nothing has been recorded
    yet" is the ordinary state before the first session, not a failure, and answering it with a 404
    puts a red error in the browser console every time the page opens — which trains the reader to
    ignore the console, where the real failures also appear.
    """
    manager = getattr(request.app.state, "session_manager", None)
    return manager.store if manager else None


@router.get("/summaries", response_model=SummariesResponse)
async def summaries(request: Request) -> dict[str, Any]:
    """The running outline of the talk."""
    store = _optional_store(request)
    return {"summaries": [s.as_event() for s in store.summaries()] if store else []}


@router.get("/polished", response_model=PolishedBlocksResponse)
async def polished(request: Request) -> dict[str, Any]:
    """The transcript rewritten for reading, oldest block first.

    Read on every page load, and empty on a machine with no language model — which is why it uses
    the optional store: "nothing has been polished" is the ordinary state, not a failure.
    """
    store = _optional_store(request)
    return {"blocks": [block.as_event() for block in store.polished_blocks()] if store else []}


@router.get("/glossary", response_model=GlossaryResponse)
async def glossary(request: Request) -> dict[str, Any]:
    """The session glossary, in order of first appearance."""
    store = _optional_store(request)
    return {"terms": [t.as_event() for t in store.glossary()] if store else []}


@router.get("/export")
async def export(request: Request, fmt: str = Query("markdown")) -> Response:
    """Download the transcript in one of the five formats."""
    store = _store(request)
    metadata = store.metadata()

    try:
        body, mime, extension = render_export(
            fmt,
            # The newest pass only. A window session that transcribed live and again afterwards
            # holds both over the same audio (D-022), and exporting their union writes the talk out
            # twice under one "Transcript" heading.
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

    stem = metadata.started_at.strftime("%Y%m%d-%H%M") if metadata else "transcript"
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{stem}-transcript.{extension}"'},
    )
