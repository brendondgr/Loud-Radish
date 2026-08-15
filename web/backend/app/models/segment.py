"""The segment — the unit the whole system agrees on (BE §8.1).

A segment is roughly a sentence or utterance. It is the unit the streaming engine emits, the
transcript store holds, the frontend renders as a paragraph, and the context pipeline chunks on.
Having one shape for all four is what keeps the boundaries between them thin.

**Times are session-absolute.** Relative timestamps never leave the streaming engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..services.asr.contract import WordToken


@dataclass(frozen=True)
class Segment:
    """One committed utterance."""

    #: Monotonic within a session, and stable. The frontend uses it for scroll anchoring and for
    #: reconnection replay, so it must never be reassigned.
    id: int
    text: str
    #: Session-absolute seconds.
    start: float
    #: Session-absolute seconds.
    end: float
    #: Real timestamp, so the user can correlate the transcript with their own notes.
    wall_clock: datetime = field(default_factory=lambda: datetime.now(UTC))
    #: Optional word-level detail, for click-to-seek and low-confidence marking.
    words: list[WordToken] = field(default_factory=list)
    #: Mean word confidence, or ``None`` when the backend does not report it.
    confidence: float | None = None
    #: Which backend and model produced this. Matters when models are swapped mid-session.
    model_id: str = ""
    #: Reserved for diarisation. Always ``None`` in v1 — present so adding it later is not a
    #: schema migration (BE §21.6).
    speaker: str | None = None
    #: Which transcription pass produced this (D-022). ``0`` is the live one, ``1`` the
    #: post-capture pass over the recorded audio. Both are kept rather than one replacing the
    #: other: the live transcript is what the user watched and what any chat citation points into.
    revision: int = 0

    @property
    def duration(self) -> float:
        """How long the utterance took."""
        return max(0.0, self.end - self.start)

    @property
    def word_count(self) -> int:
        """Words in the segment, from the text rather than the optional token list."""
        return len(self.text.split())

    def as_event(self, include_words: bool = False) -> dict[str, Any]:
        """JSON-safe payload for the ``transcript.committed`` event."""
        payload: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "revision": self.revision,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "wall_clock": self.wall_clock.isoformat(),
            "confidence": self.confidence,
            "model_id": self.model_id,
            "speaker": self.speaker,
        }
        if include_words:
            payload["words"] = [
                {
                    "text": word.text,
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                    "confidence": word.confidence,
                }
                for word in self.words
            ]
        return payload


def mean_confidence(words: list[WordToken]) -> float | None:
    """Average confidence over ``words``, or ``None`` when no word reports one.

    Returning ``None`` rather than a default matters: the frontend underlines low-confidence words,
    and a fabricated average would underline text the model was actually sure about.
    """
    scores = [word.confidence for word in words if word.confidence is not None]
    if not scores:
        return None
    return sum(scores) / len(scores)
