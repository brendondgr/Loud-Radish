"""The WebSocket endpoint (BE §12.1, §12.4).

The recovery flow, which the frontend implements against:

1. The client detects the disconnect and shows it in the status bar. **It does not clear the
   transcript** — the session is intact on the server, and wiping the display is alarming and
   unnecessary.
2. It reconnects with exponential backoff.
3. On reconnect it sends ``hello`` with the last segment id it received.
4. The server replays every committed segment after that id, then resumes live events.
5. The client ignores any segment id it already holds, which makes the replay idempotent.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .events import TRANSCRIPT_COMMITTED, TRANSCRIPT_POLISHED, envelope
from .hub import ClientConnection, EventHub

logger = logging.getLogger(__name__)

router = APIRouter()

#: Cap on a single replay. A client returning after a very long absence gets the recent tail
#: immediately rather than a multi-megabyte frame burst; it can fetch the rest over HTTP.
MAX_REPLAY_SEGMENTS = 2000


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Serve one client for the life of its connection."""
    await websocket.accept()

    hub: EventHub = websocket.app.state.hub
    client_id = uuid.uuid4().hex[:12]
    connection = hub.connect(client_id)

    writer = asyncio.create_task(_write_loop(websocket, connection))
    try:
        await _read_loop(websocket, connection, hub)
    except WebSocketDisconnect:
        logger.debug("Client %s closed the socket", client_id)
    except Exception:  # noqa: BLE001 - one bad client must not disturb the others
        logger.exception("Client %s failed", client_id)
    finally:
        hub.disconnect(client_id)
        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer


async def _read_loop(websocket: WebSocket, connection: ClientConnection, hub: EventHub) -> None:
    """Handle client-to-server frames."""
    while True:
        message = await websocket.receive_json()
        frame_type = message.get("type")

        if frame_type == "hello":
            await _handle_hello(websocket, connection, hub, message)
        elif frame_type == "ping":
            connection.push(envelope("pong", {}))
        else:
            logger.debug("Ignoring unknown client frame %r", frame_type)


async def _handle_hello(
    websocket: WebSocket,
    connection: ClientConnection,
    hub: EventHub,
    message: dict[str, Any],
) -> None:
    """Replay whatever the client missed, then let live events resume.

    ``since`` of ``null`` means a fresh client: it gets the current session state and the latest
    health events, but not the whole transcript — that is a paginated HTTP fetch, not a frame burst.
    """
    session = getattr(websocket.app.state, "session_manager", None)
    since = message.get("since")

    connection.push(envelope("session.state", session.state() if session else {"running": False}))

    if since is not None and session is not None and session.store is not None:
        replayed = session.store.segments_since(int(since), limit=MAX_REPLAY_SEGMENTS)
        for segment in replayed:
            connection.push(envelope(TRANSCRIPT_COMMITTED, segment.as_event()))

        # All of them, not just the recent ones: a polished block is produced once and never
        # re-sent, so a client that missed one would show that minute as raw text for the rest of
        # the session. There is one block a minute, so the whole set is small even for a long talk.
        for block in session.store.polished_blocks():
            connection.push(envelope(TRANSCRIPT_POLISHED, block.as_event()))

        if replayed:
            logger.info(
                "Replayed %d segments to client %s from id %s",
                len(replayed),
                connection.client_id,
                since,
            )

    # The latest health events, so the status bar is correct immediately rather than blank until
    # the next tick.
    for frame in hub.latest_state():
        connection.push(frame)


async def _write_loop(websocket: WebSocket, connection: ClientConnection) -> None:
    """Drain the client's queue onto the socket."""
    while not connection.is_closed:
        frames = await connection.drain()
        for frame in frames:
            await websocket.send_json(frame)
