"""The transport layer — the boundary the frontend builds against (BE §12).

* ``events`` — the event vocabulary, and which events may coalesce
* ``hub``    — fan-out, per-client backpressure, thread-to-loop marshalling
* ``ws``     — the WebSocket endpoint and its reconnection replay

Separate from ``routes/`` because this is a push channel with its own semantics: a disposable
connection, an idempotent replay, and a hard rule about which frames may be dropped.
"""

from . import events
from .events import ALL_EVENTS, envelope, is_coalescing
from .hub import ClientConnection, EventHub
from .ws import router as ws_router

__all__ = [
    "ALL_EVENTS",
    "ClientConnection",
    "EventHub",
    "envelope",
    "events",
    "is_coalescing",
    "ws_router",
]
