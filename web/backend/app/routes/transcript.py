"""Transcript reading, search, and export.

The reconnection path lives here too: ``/api/transcript/since/{id}`` is the HTTP equivalent of the
WebSocket replay, for a client that has been away long enough that a frame burst is the wrong shape.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from ..schemas.api import GlossaryResponse, SearchResponse, SegmentListResponse, SummariesResponse
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
) -> dict[str, Any]:
    """Everything overlapping a time range — what "summarise the last ten minutes" reads."""
    store = _store(request)
    segments = store.segments_in_range(start, end)
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


@router.get("/summaries", response_model=SummariesResponse)
async def summaries(request: Request) -> dict[str, Any]:
    """The running outline of the talk."""
    return {"summaries": [summary.as_event() for summary in _store(request).summaries()]}


@router.get("/glossary", response_model=GlossaryResponse)
async def glossary(request: Request) -> dict[str, Any]:
    """The session glossary, in order of first appearance."""
    return {"terms": [term.as_event() for term in _store(request).glossary()]}


@router.get("/export")
async def export(request: Request, fmt: str = Query("markdown")) -> Response:
    """Download the transcript in one of the five formats."""
    store = _store(request)
    metadata = store.metadata()

    try:
        body, mime, extension = render_export(
            fmt,
            segments=store.all_segments(),
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
