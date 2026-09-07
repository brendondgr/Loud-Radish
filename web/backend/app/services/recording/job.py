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
    #: Held at a window boundary, with a checkpoint written. Resumable, including after a restart
    #: of the whole application (D-045).
    PAUSED = "paused"
    #: The user said they did not want it. **The recording is kept**, and stays listed as
    #: transcribable — cancelling declines the CPU, not the audio.
    CANCELLED = "cancelled"
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
    #: The *archive key* of the transcript this pass writes into — the session file's stem,
    #: `<stamp>-<id>`. **Not the same string as `session_id`**, and the difference matters: every
    #: read path (`/api/sessions/{key}/...`, including the resume) is addressed by the key, while
    #: this job knows the id. Carrying both is cheaper than making a client join them.
    key: str = ""
    #: Where a resume would begin, in seconds of audio. Always a window boundary.
    next_start_s: float = 0.0
    #: The id the next segment will take, so a resumed pass continues the numbering rather than
    #: colliding with what is already committed.
    next_segment_id: int = 0
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
    def is_paused(self) -> bool:
        return self.state is JobState.PAUSED

    @property
    def is_resumable(self) -> bool:
        """Whether this pass could be picked up again.

        A pass still marked `running` counts, because that is what a killed process leaves behind:
        nothing wrote a terminal state because nothing got the chance.
        """
        return self.state in (JobState.RUNNING, JobState.PAUSED)

    @property
    def succeeded(self) -> bool:
        """Whether the pass completed, with or without finding speech."""
        return self.state in (JobState.DONE, JobState.DONE_NO_SPEECH)

    def advance(self, transcribed_seconds: float, segments: int) -> None:
        # Monotonic by construction: a window that overlaps the previous one must not walk the
        # figure backwards, which reads as the pass losing ground.
        self.transcribed_seconds = max(self.transcribed_seconds, transcribed_seconds)
        self.segments_written += segments

    def checkpoint(self, next_start_s: float, next_segment_id: int) -> None:
        """Note where a resume would begin. Monotonic, for the same reason `advance` is."""
        self.next_start_s = max(self.next_start_s, float(next_start_s))
        self.next_segment_id = max(self.next_segment_id, int(next_segment_id))

    def pause(self) -> None:
        """Hold at the last window boundary. The transcript so far is already committed."""
        if self.state is JobState.RUNNING:
            self.state = JobState.PAUSED

    def resume(self) -> None:
        if self.state is JobState.PAUSED:
            self.state = JobState.RUNNING
            self.finished_at = None

    def cancel(self) -> None:
        """Stop for good. **Distinct from `fail`**: nothing went wrong, and an interface that
        showed a red error for a button the user pressed on purpose would be lying to them."""
        if self.state in (JobState.RUNNING, JobState.PAUSED):
            self.state = JobState.CANCELLED
            self.finished_at = datetime.now(UTC)

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
            "key": self.key,
            "state": str(self.state),
            "progress": round(self.progress, 4),
            "transcribed_seconds": round(self.transcribed_seconds, 2),
            "total_seconds": round(self.total_seconds, 2),
            "segments": self.segments_written,
            # Where a resume would begin. Carried on the event so the interface can offer to pick
            # up a pass without a second request, and can say how much would be redone.
            "next_start_s": round(self.next_start_s, 2),
            "resumable": self.is_resumable,
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
        """Whether the slot is taken by a pass that is **actually working**.

        A *held* pass keeps the slot — nothing else may start over the same model — but it is not
        busy in the sense callers mean when they ask, which is "would starting something now
        collide". Conflating the two made a held pass refuse its own resumption with "a
        transcription is already running", found by pressing Resume in the browser (D-045).
        """
        with self._lock:
            return self._current is not None and self._current.is_running

    def claim(self, job: TranscriptionJob, *, resuming: bool = False) -> bool:
        """Take the slot for ``job``. Returns False when a pass is already running or held.

        A *held* pass still owns the slot: starting a second one over the same model while the
        first waits would make both slower and the progress figure meaningless, which is the same
        reason there is only ever one.

        ``resuming`` is how a held pass gets picked up again — it replaces a held predecessor, and
        only a held one. A pass that is genuinely running is never displaced, whoever asks.
        """
        with self._lock:
            if self._current is not None and self._current.is_resumable:
                if not (resuming and self._current.is_paused):
                    return False
            self._current = job
            return True

    def clear(self) -> None:
        """Forget a finished pass. The last one is otherwise kept, so a reload can still see it."""
        with self._lock:
            if self._current is not None and not self._current.is_resumable:
                self._current = None
