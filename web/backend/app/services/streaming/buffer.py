"""The growing audio buffer, its trimming, and timestamp rebasing (BE §7.4, §7.5).

Two responsibilities, and the second is where the bugs are.

**Trimming** deletes the audio corresponding to committed words immediately after they commit.
This is what enforces constraints **C1** and **C2** — bounded memory and bounded per-iteration
compute. Without it the buffer grows to hour-length and inference time grows with it, so the
transcriber falls progressively further behind and never recovers.

**Rebasing** is the bug you will hit. Model timestamps are relative to the submitted buffer, and
when the buffer is trimmed its start moves forward in absolute time. One monotonic value,
``buffer_start_absolute``, holds the session-relative time of sample zero, and every timestamp is
converted on the way out::

    absolute_time = buffer_start_absolute + relative_time

Relative timestamps never leave this layer.
"""

from __future__ import annotations

import numpy as np

from ..asr.contract import WordToken
from ..audio.formats import DTYPE, SAMPLE_RATE


class StreamBuffer:
    """Accumulates canonical-format audio and converts between relative and absolute time."""

    def __init__(self, retained_context_s: float = 0.75, sample_rate: int = SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._retained = max(0.0, retained_context_s)
        self._samples = np.zeros(0, dtype=DTYPE)
        self._buffer_start_absolute = 0.0
        self._session_seconds = 0.0

    # -- accumulation --------------------------------------------------------------

    def append(self, frame: np.ndarray) -> None:
        """Add one frame of canonical-format audio."""
        array = np.ascontiguousarray(frame, dtype=DTYPE).ravel()
        if array.size == 0:
            return
        self._samples = np.concatenate([self._samples, array])
        self._session_seconds += array.size / self._sample_rate

    @property
    def audio(self) -> np.ndarray:
        """The unconfirmed buffer, ready to submit for inference."""
        return self._samples

    @property
    def duration(self) -> float:
        """Length of the buffer in seconds."""
        return self._samples.size / self._sample_rate

    @property
    def session_seconds(self) -> float:
        """Total audio seen this session.

        Derived from audio consumed rather than wall-clock, so a file source running faster than
        real time produces the same timestamps it would live, and tests are deterministic.
        """
        return self._session_seconds

    @property
    def buffer_start_absolute(self) -> float:
        """Session-absolute time of sample zero. Monotonic — it only ever moves forward."""
        return self._buffer_start_absolute

    @property
    def end_absolute(self) -> float:
        """Session-absolute time of the last sample in the buffer."""
        return self._buffer_start_absolute + self.duration

    # -- rebasing ------------------------------------------------------------------

    def to_absolute(self, relative: float) -> float:
        """Convert a buffer-relative time to session-absolute."""
        return self._buffer_start_absolute + relative

    def to_relative(self, absolute: float) -> float:
        """Convert a session-absolute time to buffer-relative. May be negative before the buffer."""
        return absolute - self._buffer_start_absolute

    def rebase(self, words: list[WordToken]) -> list[WordToken]:
        """Rewrite a pass's word timestamps from buffer-relative into session-absolute.

        Every word leaving the engine goes through here. Nothing downstream — the transcript store,
        the transport events, the frontend — ever sees a relative time.
        """
        offset = self._buffer_start_absolute
        return [
            WordToken(
                text=word.text,
                start=word.start + offset,
                end=word.end + offset,
                confidence=word.confidence,
            )
            for word in words
        ]

    # -- trimming ------------------------------------------------------------------

    def trim_to(self, absolute_time: float) -> float:
        """Discard audio before ``absolute_time``, keeping the retained context tail.

        Args:
            absolute_time: session-absolute time to cut at — normally the end of the last committed
                word, preferably at a sentence boundary so the retained buffer starts cleanly and
                the model has coherent context.

        Returns:
            Seconds of audio actually removed. Zero when the cut point is already behind the
            buffer's start, which keeps the operation idempotent.
        """
        target = absolute_time - self._retained
        if target <= self._buffer_start_absolute:
            return 0.0

        # Never trim past the audio actually held: a cut point beyond the buffer would leave the
        # start ahead of the data and every subsequent timestamp wrong.
        target = min(target, self.end_absolute)

        remove_seconds = target - self._buffer_start_absolute
        remove_samples = min(self._samples.size, int(round(remove_seconds * self._sample_rate)))
        if remove_samples <= 0:
            return 0.0

        self._samples = self._samples[remove_samples:].copy()
        self._buffer_start_absolute += remove_samples / self._sample_rate
        return remove_samples / self._sample_rate

    def hard_trim(self, keep_seconds: float) -> float:
        """Keep only the most recent ``keep_seconds``, discarding the rest.

        The maximum-buffer guard's escape hatch. Used when the buffer has grown past what the model
        can accept and there is no natural boundary to cut at.
        """
        keep_samples = max(0, int(round(keep_seconds * self._sample_rate)))
        if self._samples.size <= keep_samples:
            return 0.0

        removed = self._samples.size - keep_samples
        self._samples = self._samples[removed:].copy()
        self._buffer_start_absolute += removed / self._sample_rate
        return removed / self._sample_rate

    def clear(self) -> None:
        """Drop the buffer, advancing its start so absolute time stays continuous.

        Used on a model swap: the audio is discarded but the session clock is not rewound, because
        the transcript's timeline must stay consistent with what the user already read.
        """
        self._buffer_start_absolute = self.end_absolute
        self._samples = np.zeros(0, dtype=DTYPE)

    def reset(self) -> None:
        """Return to a clean state for a new session."""
        self._samples = np.zeros(0, dtype=DTYPE)
        self._buffer_start_absolute = 0.0
        self._session_seconds = 0.0
