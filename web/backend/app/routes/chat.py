"""Asking the assistant about the transcript.

``POST /api/chat/send`` returns as soon as the request is accepted; the answer arrives as
``chat.delta`` frames on the WebSocket and ends with ``chat.done``. That split is deliberate — the
answer takes tens of seconds from a local model, and a request held open for that long is a request
that a proxy, a sleeping laptop, or an impatient reload will break.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..schemas.api import (
    ChatHistoryResponse,
    ChatSendRequest,
    ChatSendResponse,
    QuickActionsResponse,
)
from ..services.chat import ChatError, ChatRequest

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _chat(request: Request):  # noqa: ANN201 - returns ChatService
    service = getattr(request.app.state, "chat", None)
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "not-ready", "message": "The assistant is starting."}},
        )
    return service


@router.post("/send", response_model=ChatSendResponse)
async def send(request: Request, body: ChatSendRequest) -> dict[str, Any]:
    """Ask a question. The answer streams over the WebSocket."""
    try:
        return await _chat(request).ask(
            ChatRequest(
                message=body.message,
                action=body.action,
                quote=body.quote,
                quote_start=body.quote_start,
            )
        )
    except ChatError as exc:
        # 409 rather than 400: the request is well-formed, it just conflicts with the current state
        # — nothing recorded yet, no model chosen, or an answer already in flight.
        raise HTTPException(
            status_code=409, detail={"error": {"code": "chat-unavailable", "message": str(exc)}}
        ) from exc


@router.post("/cancel")
async def cancel(request: Request) -> dict[str, Any]:
    """Stop the in-flight answer. Cancelling nothing is not an error."""
    return {"cancelled": await _chat(request).cancel()}


@router.get("/history", response_model=ChatHistoryResponse)
async def history(request: Request) -> dict[str, Any]:
    """The conversation so far."""
    return {"messages": _chat(request).history()}


@router.delete("/history")
async def clear_history(request: Request) -> dict[str, Any]:
    """Clear the conversation. The transcript is untouched."""
    _chat(request).clear_history()
    return {"cleared": True}


@router.get("/quick-actions", response_model=QuickActionsResponse)
async def quick_actions(request: Request) -> dict[str, Any]:
    """The configured one-tap prompts."""
    return {"actions": [action.model_dump() for action in _chat(request).quick_actions()]}


@router.post("/read")
async def mark_read(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record how far the user has read, so "what did I miss" has a starting point.

    Held on the server rather than in the browser so it survives a reload — losing it mid-talk
    turns the action into "summarise everything", which is a different and much less useful thing.
    """
    position = (body or {}).get("position")
    return {"position": _chat(request).mark_read(position)}
