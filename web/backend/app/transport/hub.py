"""The WebSocket hub — fan-out, backpressure, and reconnection replay (BE §12.4).

The socket is **disposable by design**. A client that drops reconnects, says which segment it
last received, and the server replays everything after it. That single decision removes an entire
class of bug: no reasoning about what a client might have missed, because the client tells you.

Three properties the hub guarantees:

* **Committed segments are never dropped.** Losing one loses transcript.
* **Health events coalesce.** A stale level-meter frame is worse than none — it draws a wrong bar.
* **A slow client cannot stall the pipeline.** Each connection has its own bounded queue, and the
  emitting thread never waits on a socket.
* **A retained event is dropped once it stops being true.** The newest instance of each coalescing
  event is replayed to every client that connects. A finished transcription pass whose last progress
  frame said *running* would otherwise keep telling new clients it was running for the life of the
  process, which is exactly how the interface ended up stuck on "Transcribing… 100%".
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

from .events import CRITICAL_EVENTS, envelope, invalidated_by, is_coalescing

logger = logging.getLogger(__name__)

#: Per-connection outbound queue depth. Deep enough to ride out a browser tab being backgrounded,
#: shallow enough that a genuinely dead client is noticed.
CLIENT_QUEUE_DEPTH = 512


class ClientConnection:
    """One connected client's outbound queue.

    The pipeline pushes here from a worker thread; the socket's writer task drains it on the event
    loop. The two never block each other.
    """

    def __init__(self, client_id: str, depth: int = CLIENT_QUEUE_DEPTH) -> None:
        self.client_id = client_id
        self._queue: deque[dict[str, Any]] = deque()
        self._depth = depth
        self._ready = asyncio.Event()
        self._closed = False
        self.dropped = 0

    def push(self, frame: dict[str, Any]) -> None:
        """Queue a frame. Called from the event loop thread only."""
        if self._closed:
            return

        event = frame.get("event", "")
        if is_coalescing(event):
            self._replace_latest(event, frame)
        else:
            self._queue.append(frame)

        self._enforce_depth()
        self._ready.set()

    def _replace_latest(self, event: str, frame: dict[str, Any]) -> None:
        """Keep only the newest instance of a coalescing event."""
        for index in range(len(self._queue) - 1, -1, -1):
            if self._queue[index].get("event") == event:
                self._queue[index] = frame
                return
        self._queue.append(frame)

    def _enforce_depth(self) -> None:
        """Trim the backlog, sacrificing non-critical frames first.

        A client far enough behind to overflow this is one whose level meter is already meaningless,
        so health events go before anything that carries transcript.
        """
        while len(self._queue) > self._depth:
            for index, frame in enumerate(self._queue):
                if frame.get("event") not in CRITICAL_EVENTS:
                    del self._queue[index]
                    self.dropped += 1
                    break
            else:
                # Everything queued is critical. Drop the oldest and say so loudly: this means a
                # client has been unreachable long enough to lose transcript.
                self._queue.popleft()
                self.dropped += 1
                logger.warning(
                    "Client %s is so far behind that committed segments were dropped",
                    self.client_id,
                )

    async def drain(self) -> list[dict[str, Any]]:
        """Wait for frames and return everything queued."""
        await self._ready.wait()
        frames = list(self._queue)
        self._queue.clear()
        self._ready.clear()
        return frames

    def close(self) -> None:
        """Stop accepting frames and wake the writer."""
        self._closed = True
        self._ready.set()

    @property
    def is_closed(self) -> bool:
        """Whether this connection has been closed."""
        return self._closed

    @property
    def depth(self) -> int:
        """How many frames are waiting."""
        return len(self._queue)


class EventHub:
    """Fans pipeline events out to every connected client."""

    def __init__(self) -> None:
        self._clients: dict[str, ClientConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        #: The most recent instance of each coalescing event, replayed to a client on connect so it
        #: paints a correct screen immediately rather than waiting for the next tick.
        self._latest: dict[str, dict[str, Any]] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the event loop that pipeline events must be marshalled onto."""
        self._loop = loop

    # -- connections ---------------------------------------------------------------

    def connect(self, client_id: str) -> ClientConnection:
        """Register a client and return its queue."""
        connection = ClientConnection(client_id)
        self._clients[client_id] = connection
        logger.info("Client %s connected (%d total)", client_id, len(self._clients))
        return connection

    def disconnect(self, client_id: str) -> None:
        """Remove a client. Its transcript is untouched — the socket is disposable."""
        connection = self._clients.pop(client_id, None)
        if connection is not None:
            connection.close()
            logger.info("Client %s disconnected (%d remain)", client_id, len(self._clients))

    @property
    def client_count(self) -> int:
        """How many clients are connected."""
        return len(self._clients)

    def latest_state(self) -> list[dict[str, Any]]:
        """The most recent health events, so a new client paints a correct screen at once.

        Only events still true: a terminal event removes the coalescing one it ended, so nothing
        here describes something that has already finished.
        """
        return list(self._latest.values())

    def forget(self, *events: str) -> None:
        """Drop retained coalescing events by name. Used when a new session starts."""
        for event in events:
            self._latest.pop(event, None)

    # -- publishing ----------------------------------------------------------------

    def emit(self, event: str, data: dict[str, Any]) -> None:
        """Publish one event. **Safe to call from any thread.**

        This is the function the session manager was handed. Marshalling onto the loop happens here
        so that nothing in the audio path has to know an event loop exists.
        """
        frame = envelope(event, data)
        if is_coalescing(event):
            self._latest[event] = frame
        # A terminal event retracts the progress it terminates, so the replay set never carries a
        # state that has already ended.
        for stale in invalidated_by(event):
            self._latest.pop(stale, None)

        loop = self._loop
        if loop is None or loop.is_closed():
            # No loop bound yet — during start-up, or in a test driving the pipeline directly.
            self._deliver(frame)
            return

        try:
            loop.call_soon_threadsafe(self._deliver, frame)
        except RuntimeError:
            # The loop closed between the check and the call, which happens during shutdown.
            logger.debug("Dropped %s: the event loop is closing", event)

    def _deliver(self, frame: dict[str, Any]) -> None:
        """Push a frame to every client. Runs on the event loop."""
        for connection in list(self._clients.values()):
            connection.push(frame)
