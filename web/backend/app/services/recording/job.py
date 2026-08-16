"""The state of one transcription pass over a finished recording (D-021).

A pass over a forty-minute talk can run for half an hour. That is long enough that the user will
leave, reload the page, and come back — so its progress has to live somewhere the server can be
asked about, rather than in a variable in one browser tab.

Deliberately **not persisted**. A server restart loses the job and keeps the recording, and the next
start lists any recording without a transcript as unfinished with a button to run it. That falls out
of writing the file first and is much cheaper than a durable job table for something that is
recoverable by re-running it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class JobState(StrEnum):
    """Where a transcription pass has got to."""

    RUNNING = "running"
    DONE = "done"
    #: Finished, correctly, and found nothing to transcribe. **Distinct from `done` on purpose.**
    #: "322 seconds processed, 2 segments" is what a broken transcriber looks like and what an
    #: accurate one looks like on music — and an interface that cannot tell them apart shows an
    #: empty transcript that reads as a crash. This state lets it say "no speech detected" instead,
    #: which is the difference between a bug report and an understanding.
    DONE_NO_SPEECH = "done_no_speech"
    FAILED = "failed"


@dataclass
class TranscriptionJob:
    """One pass over one recording."""

    session_id: str
    #: The recording being transcribed. Named in every failure message, because when a pass fails
    #: this file is the only remaining copy of what was said.
    source_path: str
    #: Seconds of audio in the recording. The denominator of every progress figure.
    total_seconds: float
    state: JobState = JobState.RUNNING
    #: Seconds of audio transcribed so far. Progress is reported by audio position because it is
    #: honest, monotonic, and needs no instrumentation inside the model.
    transcribed_seconds: float = 0.0
    segments_written: int = 0
    error: str = ""
    #: What the audio actually is, measured before the pass runs (`characterise.py`). Carried on
    #: the event so the interface can explain an empty transcript without a second request.
    audio: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def progress(self) -> float:
        """0–1. Clamped, because a final window may overrun the file's nominal length."""
        if self.total_seconds <= 0:
            return 1.0 if self.state is not JobState.RUNNING else 0.0
        return min(1.0, max(0.0, self.transcribed_seconds / self.total_seconds))

    @property
    def is_running(self) -> bool:
        return self.state is JobState.RUNNING

    @property
    def succeeded(self) -> bool:
        """Whether the pass completed, with or without finding speech."""
        return self.state in (JobState.DONE, JobState.DONE_NO_SPEECH)

    def advance(self, transcribed_seconds: float, segments: int) -> None:
        # Monotonic by construction: a window that overlaps the previous one must not walk the
        # figure backwards, which reads as the pass losing ground.
        self.transcribed_seconds = max(self.transcribed_seconds, transcribed_seconds)
        self.segments_written += segments

    def finish(self, *, found_speech: bool = True) -> None:
        """End the pass. ``found_speech`` decides which of the two success states applies."""
        self.state = JobState.DONE if found_speech else JobState.DONE_NO_SPEECH
        self.transcribed_seconds = self.total_seconds
        self.finished_at = datetime.now(UTC)

    def fail(self, message: str) -> None:
        self.state = JobState.FAILED
        self.error = message
        self.finished_at = datetime.now(UTC)

    def as_event(self) -> dict[str, Any]:
        """The payload for ``transcription.progress`` / ``.done`` / ``.failed``."""
        return {
            "session_id": self.session_id,
            "state": str(self.state),
            "progress": round(self.progress, 4),
            "transcribed_seconds": round(self.transcribed_seconds, 2),
            "total_seconds": round(self.total_seconds, 2),
            "segments": self.segments_written,
            "error": self.error,
            "audio": dict(self.audio),
        }


class JobRegistry:
    """Holds the current pass, and enforces that there is only ever one.

    One at a time is not an arbitrary limit. A second pass would contend for the same model on the
    same device, and two transcriptions each running at half speed finish later than two run in
    sequence — while also making the progress figure meaningless.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: TranscriptionJob | None = None

    @property
    def current(self) -> TranscriptionJob | None:
        with self._lock:
            return self._current

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.is_running

    def claim(self, job: TranscriptionJob) -> bool:
        """Take the slot for ``job``. Returns False when a pass is already running."""
        with self._lock:
            if self._current is not None and self._current.is_running:
                return False
            self._current = job
            return True

    def clear(self) -> None:
        """Forget a finished pass. The last one is otherwise kept, so a reload can still see it."""
        with self._lock:
            if self._current is not None and not self._current.is_running:
                self._current = None
