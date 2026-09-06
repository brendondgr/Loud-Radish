"""One export, as something with stages you can watch (D-037).

Reported: "press export and watch the pipeline run through its stages… show progress and estimated
time for each stage… instead of a black box." So a stage is a first-class thing here rather than a
spinner with a label.

Modelled on `services/recording/job.py`, deliberately and closely: the frontend already knows how to
consume that shape, `TranscriptionJob.as_event()` is the contract the recording store reads, and a
second progress vocabulary that meant the same thing would be one more thing to keep in step.

**The overall figure is weighted by predicted cost, not by stage count.** Four stages of which one
takes four minutes and three take a second each would otherwise sit at 25 % for the whole encode and
then jump — which is the specific way a progress bar becomes a thing people stop believing. The
weights come from `estimate.py`, which has already predicted what each stage will cost, so the bar
moves at roughly a constant rate through work that is nothing like evenly divided.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ExportState(StrEnum):
    """Where one export has got to."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    #: Asked to stop, and stopped. Distinct from `FAILED` because nothing went wrong.
    CANCELLED = "cancelled"


class StageState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    #: Nothing to do — an export with no re-encode never runs the encode stage, and a stage that
    #: says "skipped" is more honest than one that flashes to 100 % having done nothing.
    SKIPPED = "skipped"


@dataclass
class ExportStage:
    """One step of the pipeline, and how far through it is."""

    id: str
    label: str
    #: Share of the whole job this stage is predicted to take, between 0 and 1.
    weight: float = 0.0
    #: Predicted seconds, from `estimate.py`. Zero when nothing predicted it.
    predicted_s: float = 0.0
    state: StageState = StageState.WAITING
    progress: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    detail: str = ""

    @property
    def elapsed_s(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.monotonic()
        return max(0.0, end - self.started_at)

    @property
    def remaining_s(self) -> float:
        """Seconds left, from what this stage has actually done rather than from the prediction.

        A prediction is what you have before the work starts. Once a stage is a third of the way
        through in twenty seconds, its own rate is a better answer than any constant — and it is the
        answer that corrects itself on a machine slower than the one the constants came from.
        """
        if self.state is not StageState.RUNNING:
            return 0.0
        if self.progress > 0.02 and self.elapsed_s > 1.0:
            return max(0.0, self.elapsed_s / self.progress - self.elapsed_s)
        return max(0.0, self.predicted_s * (1.0 - self.progress))

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "state": str(self.state),
            "progress": round(min(1.0, max(0.0, self.progress)), 4),
            "elapsed_s": round(self.elapsed_s, 1),
            "remaining_s": round(self.remaining_s, 1),
            "predicted_s": round(self.predicted_s, 1),
            "detail": self.detail,
        }


@dataclass
class ExportJob:
    """One export of one session, from measuring it to a file on disk."""

    id: str
    key: str
    preset_id: str
    stages: list[ExportStage] = field(default_factory=list)
    state: ExportState = ExportState.RUNNING
    error: str = ""
    #: Where the finished archive landed, once there is one.
    output_path: str = ""
    output_bytes: int = 0
    #: What was predicted before any of it ran, so the result can be read against the promise.
    estimated_bytes: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    _started_monotonic: float = field(default_factory=time.monotonic)
    _finished_monotonic: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- reading -----------------------------------------------------------------------

    def stage(self, stage_id: str) -> ExportStage | None:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        return None

    @property
    def progress(self) -> float:
        """The whole job, weighted by what each stage was predicted to cost."""
        total = sum(stage.weight for stage in self.stages)
        if total <= 0.0:
            return 0.0
        done = sum(stage.weight * min(1.0, max(0.0, stage.progress)) for stage in self.stages)
        return min(1.0, done / total)

    @property
    def elapsed_s(self) -> float:
        """How long this export took, and **frozen once it is over**.

        A finished job whose elapsed time keeps climbing reports "took 14 s" to the window that
        watched it and "took 1 min" to the one opened a minute later, about the same file. The same
        fault the session clock had (D-033) and the same remedy: the end is a fact, so it is stored.
        """
        end = self._finished_monotonic or time.monotonic()
        return max(0.0, end - self._started_monotonic)

    @property
    def remaining_s(self) -> float:
        """This stage's remainder, plus what every stage after it is predicted to take."""
        remaining = 0.0
        seen_running = False
        for stage in self.stages:
            if stage.state is StageState.RUNNING:
                seen_running = True
                remaining += stage.remaining_s
            elif stage.state is StageState.WAITING:
                remaining += stage.predicted_s
        return remaining if seen_running or remaining else 0.0

    # -- writing -----------------------------------------------------------------------

    def begin(self, stage_id: str, detail: str = "") -> None:
        with self._lock:
            stage = self.stage(stage_id)
            if stage is None:
                return
            stage.state = StageState.RUNNING
            stage.started_at = time.monotonic()
            stage.detail = detail

    def advance(self, stage_id: str, progress: float) -> None:
        """Move a stage forward. **Never backward** — a bar that retreats reads as a fault."""
        with self._lock:
            stage = self.stage(stage_id)
            if stage is None:
                return
            stage.progress = max(stage.progress, min(1.0, max(0.0, progress)))

    def complete(self, stage_id: str, detail: str = "") -> None:
        with self._lock:
            stage = self.stage(stage_id)
            if stage is None:
                return
            stage.state = StageState.DONE
            stage.progress = 1.0
            stage.finished_at = time.monotonic()
            if detail:
                stage.detail = detail

    def skip(self, stage_id: str, detail: str = "") -> None:
        with self._lock:
            stage = self.stage(stage_id)
            if stage is None:
                return
            stage.state = StageState.SKIPPED
            stage.progress = 1.0
            stage.finished_at = time.monotonic()
            stage.detail = detail

    def finish(self, output_path: str, output_bytes: int) -> None:
        with self._lock:
            self.state = ExportState.DONE
            self.output_path = output_path
            self.output_bytes = output_bytes
            self.finished_at = datetime.now(UTC)
            self._finished_monotonic = time.monotonic()

    def fail(self, message: str, stage_id: str = "") -> None:
        with self._lock:
            self.state = ExportState.FAILED
            self.error = message
            self.finished_at = datetime.now(UTC)
            self._finished_monotonic = time.monotonic()
            stage = self.stage(stage_id) if stage_id else None
            if stage is not None:
                stage.state = StageState.FAILED
                stage.finished_at = time.monotonic()
                stage.detail = message

    def cancel(self) -> None:
        with self._lock:
            if self.state is ExportState.RUNNING:
                self.state = ExportState.CANCELLED
                self.finished_at = datetime.now(UTC)
                self._finished_monotonic = time.monotonic()

    @property
    def cancelled(self) -> bool:
        return self.state is ExportState.CANCELLED

    # -- the wire ----------------------------------------------------------------------

    def as_event(self) -> dict[str, Any]:
        """The `export.progress` / `.done` / `.failed` payload.

        Same shape whichever event carries it, exactly as `TranscriptionJob.as_event()` is: a client
        that has one handler for the payload and a separate question about which event arrived is a
        client that cannot get the two out of step.
        """
        return {
            "id": self.id,
            "key": self.key,
            "preset": self.preset_id,
            "state": str(self.state),
            "progress": round(self.progress, 4),
            "elapsed_s": round(self.elapsed_s, 1),
            "remaining_s": round(self.remaining_s, 1),
            "stages": [stage.as_dict() for stage in self.stages],
            "estimated_bytes": self.estimated_bytes,
            "output_bytes": self.output_bytes,
            "error": self.error,
        }


class ExportRegistry:
    """One export at a time, and the last one's result kept for collection.

    **Held after it finishes, unlike the transcription registry.** An export produces a file the
    user then has to download, and a page reloaded between "done" and the click that fetches it must
    still be able to find what it made.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: ExportJob | None = None

    def claim(self, job: ExportJob) -> bool:
        with self._lock:
            if self._current is not None and self._current.state is ExportState.RUNNING:
                return False
            self._current = job
            return True

    def current(self) -> ExportJob | None:
        with self._lock:
            return self._current

    def find(self, job_id: str) -> ExportJob | None:
        with self._lock:
            current = self._current
        return current if current is not None and current.id == job_id else None

    @property
    def is_busy(self) -> bool:
        with self._lock:
            return self._current is not None and self._current.state is ExportState.RUNNING

    def clear(self) -> None:
        with self._lock:
            self._current = None
