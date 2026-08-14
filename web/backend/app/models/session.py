"""Session-level records (BE §8.1, §9.2, §9.3).

The metadata a session carries, plus the three things the context pipeline accumulates alongside the
transcript: summaries, glossary terms, and the user's own conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ChatRole = Literal["user", "assistant", "system"]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class SessionMetadata:
    """What a session is, beyond its transcript."""

    session_id: str
    started_at: datetime = field(default_factory=_now)
    ended_at: datetime | None = None
    title: str = ""
    venue: str = ""
    speaker: str = ""
    #: The biasing prompt the user supplied before the talk (BE §6.5).
    session_prompt: str = ""
    #: Snapshot of the configuration the session ran with, for reproducibility.
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def is_running(self) -> bool:
        """Whether the session is still recording."""
        return self.ended_at is None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for the session endpoints."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "title": self.title,
            "venue": self.venue,
            "speaker": self.speaker,
            "session_prompt": self.session_prompt,
            "running": self.is_running,
        }


@dataclass(frozen=True)
class Summary:
    """One entry in the running outline of the talk."""

    id: int
    start: float
    end: float
    text: str
    created_at: datetime = field(default_factory=_now)

    def as_event(self) -> dict[str, Any]:
        """The ``summary.added`` payload."""
        return {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }


@dataclass(frozen=True)
class GlossaryTerm:
    """A technical term, with a plain-language definition and when it first appeared.

    For an unfamiliar field this is frequently more useful than the summaries — much of feeling lost
    in a colloquium is not knowing three nouns.
    """

    term: str
    definition: str
    first_seen: float
    created_at: datetime = field(default_factory=_now)

    def as_event(self) -> dict[str, Any]:
        """The ``glossary.added`` payload."""
        return {
            "term": self.term,
            "definition": self.definition,
            "first_seen": round(self.first_seen, 3),
        }


@dataclass(frozen=True)
class ChatMessage:
    """One turn of the user's conversation with the assistant."""

    id: int
    role: ChatRole
    text: str
    created_at: datetime = field(default_factory=_now)
    #: Transcript position the answer was based on. Transcription continues during generation, so an
    #: answer is always slightly stale; making that visible prevents confusion (BE §11.4).
    context_timestamp: float | None = None
    #: Cited timestamps, a quoted selection, and which context tiers were used.
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for the chat history endpoint."""
        return {
            "id": self.id,
            "role": self.role,
            "text": self.text,
            "created_at": self.created_at.isoformat(),
            "context_timestamp": self.context_timestamp,
            "meta": self.meta,
        }


@dataclass(frozen=True)
class SessionStats:
    """Totals for the status display and for token budgeting."""

    segment_count: int
    word_count: int
    duration_seconds: float
    summary_count: int = 0
    glossary_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload."""
        return {
            "segments": self.segment_count,
            "words": self.word_count,
            "duration_s": round(self.duration_seconds, 2),
            "summaries": self.summary_count,
            "glossary_terms": self.glossary_count,
        }
