"""What the streaming engine emits.

The **committed / hypothesis** distinction is the most important part of the whole contract, and it
is expressed here so that both the LocalAgreement path and the streaming-native bypass path produce
identical output. Nothing downstream can tell which engine produced an event.

* :class:`CommittedSegment` — **append** permanently. Never modified once emitted (constraint C4).
* :class:`HypothesisUpdate` — **replace** the tentative tail wholly. Not a list entry.

Treating the hypothesis as the last item in the segment list duplicates text on screen. It is a
single mutable element that always sits at the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...models.segment import Segment
from .guards import Severity


@dataclass(frozen=True)
class CommittedSegment:
    """A finished segment. Append it; it will never change."""

    segment: Segment

    def as_event(self) -> dict[str, Any]:
        """The ``transcript.committed`` payload."""
        return {"event": "transcript.committed", "data": self.segment.as_event()}


@dataclass(frozen=True)
class HypothesisUpdate:
    """The tentative tail. Replace whatever is currently displayed with this.

    An empty ``text`` is meaningful, not a no-op: it means the tail has been committed or discarded
    and the display should clear.
    """

    text: str
    start: float

    def as_event(self) -> dict[str, Any]:
        """The ``transcript.hypothesis`` payload."""
        return {
            "event": "transcript.hypothesis",
            "data": {"text": self.text, "start": round(self.start, 3)},
        }


@dataclass(frozen=True)
class EngineNotice:
    """Something the user needs to know, phrased as what to do about it."""

    code: str
    message: str
    severity: Severity = Severity.WARNING

    def as_event(self) -> dict[str, Any]:
        """The ``error`` payload. Info-severity notices are status-bar text, not errors."""
        return {
            "event": "error",
            "data": {
                "code": self.code,
                "message": self.message,
                "severity": str(self.severity),
            },
        }


#: Anything the engine can emit.
EngineEvent = CommittedSegment | HypothesisUpdate | EngineNotice
