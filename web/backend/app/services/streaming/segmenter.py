"""Grouping committed words into segments (BE §7.7).

Committed words alone are not a readable transcript. They become one when grouped into segments —
roughly sentences or utterances — which is the unit the store holds, the frontend renders as a
paragraph, and the context pipeline chunks on.

Three boundaries, in the order they are trusted:

1. **Sentence-terminating punctuation**, where the model emits it. The most reliable signal.
2. **VAD pause events.** A speaker who pauses has finished a thought, even without punctuation.
3. **A maximum duration cap.** So an unpunctuated monologue still breaks into readable paragraphs
   rather than becoming one wall of text an hour long.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...models.segment import Segment, mean_confidence
from ..asr.contract import WordToken

#: Characters that end a sentence. Closing quotes and brackets may follow them.
TERMINATORS = ".!?…"
TRAILING = "\"')]}»”’"

#: An abbreviation ending in a period is not a sentence boundary. Kept short deliberately: a missed
#: boundary costs a slightly long paragraph, whereas over-eager matching splits mid-sentence.
ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "fig",
        "eq",
        "cf",
        "al",
        "no",
        "approx",
    }
)


def ends_sentence(text: str) -> bool:
    """Whether ``text`` terminates a sentence."""
    stripped = text.strip().rstrip(TRAILING)
    if not stripped or stripped[-1] not in TERMINATORS:
        return False

    if stripped[-1] == ".":
        body = stripped[:-1].lower()
        if body in ABBREVIATIONS:
            return False
        # A single initial — "J." in "J. Smith" — is not a sentence end either.
        if len(body) == 1 and body.isalpha():
            return False
    return True


class Segmenter:
    """Accumulates committed words and emits completed segments."""

    def __init__(self, max_segment_s: float = 30.0, first_id: int = 1) -> None:
        self._max_segment_s = max_segment_s
        self._next_id = first_id
        self._words: list[WordToken] = []
        self._model_id = ""

    # -- state ---------------------------------------------------------------------

    @property
    def next_id(self) -> int:
        """The id the next completed segment will receive."""
        return self._next_id

    @property
    def pending_words(self) -> list[WordToken]:
        """Committed words not yet grouped into a segment."""
        return list(self._words)

    @property
    def has_pending(self) -> bool:
        """Whether any committed words are waiting for a boundary."""
        return bool(self._words)

    # -- accumulation --------------------------------------------------------------

    def add(
        self,
        words: list[WordToken],
        model_id: str = "",
        pause_boundary: bool = False,
    ) -> list[Segment]:
        """Add committed words and return any segments they completed.

        Args:
            words: committed words with session-absolute timestamps.
            model_id: which model produced them, recorded on the segment.
            pause_boundary: whether the VAD reported a pause at the end of this batch.
        """
        if model_id:
            self._model_id = model_id

        completed: list[Segment] = []
        for word in words:
            self._words.append(word)
            if ends_sentence(word.text) or self._over_duration():
                completed.append(self._emit())

        if pause_boundary and self._words:
            completed.append(self._emit())

        return completed

    def flush(self) -> Segment | None:
        """Emit whatever is pending, regardless of boundary.

        Called when a session stops or a model is swapped, so the last words spoken are not lost
        waiting for a full stop that never arrives.
        """
        return self._emit() if self._words else None

    def reset(self, first_id: int | None = None) -> None:
        """Discard pending words and optionally restart the id sequence."""
        self._words = []
        if first_id is not None:
            self._next_id = first_id

    def update_max_duration(self, max_segment_s: float) -> None:
        """Adopt a new duration cap; it is a live setting."""
        self._max_segment_s = max_segment_s

    # -- internals -----------------------------------------------------------------

    def _over_duration(self) -> bool:
        """Whether the pending words have run past the duration cap."""
        if not self._words:
            return False
        return (self._words[-1].end - self._words[0].start) >= self._max_segment_s

    def _emit(self) -> Segment:
        """Turn the pending words into a segment and start a new one."""
        words = self._words
        self._words = []

        segment = Segment(
            id=self._next_id,
            text=" ".join(word.text for word in words).strip(),
            start=words[0].start,
            end=words[-1].end,
            wall_clock=datetime.now(UTC),
            words=words,
            confidence=mean_confidence(words),
            model_id=self._model_id,
        )
        self._next_id += 1
        return segment
