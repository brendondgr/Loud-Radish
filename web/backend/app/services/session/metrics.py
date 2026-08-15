"""Pipeline health, gathered in one place (BE §16.3).

Six numbers say whether the system is working. They are gathered here rather than left scattered
because the status bar shows them together and a user glancing at the screen needs one coherent
picture, not six independently-sampled ones.

**Real-time factor is the single most important number.** Below 1.0 the queue grows without bound
and the transcriber will never catch up on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..audio.ring_buffer import RingBufferStats
from ..streaming.engine import EngineMetrics
from .workers import QueueStats


@dataclass
class PipelineMetrics:
    """Everything the status event and the health panel need."""

    engine: EngineMetrics = field(default_factory=EngineMetrics)
    queue: QueueStats | None = None
    ring_buffer: RingBufferStats | None = None
    model_id: str = ""
    device: str = ""
    #: How the audio arrived — a device name, or a file name.
    source: str = ""
    detector: str = ""
    #: Passes discarded as text the speech model invented rather than heard. Published because a
    #: filter that deletes speech invisibly is a worse bug than the one it fixes — a number rising
    #: steadily while someone is talking is how you find out the thresholds are too tight.
    suppressed: int = 0

    @property
    def real_time_factor(self) -> float:
        """Audio seconds processed per wall-clock second inside the model."""
        return self.engine.real_time_factor

    @property
    def dropped_frames(self) -> int:
        """Audio lost to a full queue or an overrun ring buffer. Should be zero."""
        queue_drops = self.queue.dropped if self.queue else 0
        buffer_drops = self.ring_buffer.dropped_events if self.ring_buffer else 0
        return queue_drops + buffer_drops

    @property
    def queue_fill(self) -> float:
        """How full the capture queue is, 0.0–1.0."""
        return self.queue.fill if self.queue else 0.0

    @property
    def is_healthy(self) -> bool:
        """Whether the pipeline is keeping up.

        A factor of zero means no inference has run yet — a session that has just started, or one
        where the speaker has not spoken. That is not unhealthy, and reporting it as such would put
        a red indicator on screen before the talk begins.
        """
        if self.real_time_factor == 0.0:
            return True
        return self.real_time_factor >= 1.0 and self.dropped_frames == 0

    def as_event(self) -> dict[str, Any]:
        """The ``status`` event payload (BE §12.2)."""
        return {
            "rtf": round(self.real_time_factor, 2),
            "queue_depth": self.queue.depth if self.queue else 0,
            "queue_fill": round(self.queue_fill, 3),
            "commit_latency_s": round(self.engine.median_commit_latency, 2),
            "commit_latency_p95_s": round(self.engine.p95_commit_latency, 2),
            "model_id": self.model_id,
            "device": self.device,
            "source": self.source,
            "detector": self.detector,
            "dropped_frames": self.dropped_frames,
            "suppressed": self.suppressed,
            "audio_seconds": round(self.engine.audio_seconds, 1),
            "committed_words": self.engine.committed_words,
            "correction_rate": round(self.engine.correction_rate, 3),
            "forced_commit_rate": round(self.engine.forced_commit_rate, 3),
            "healthy": self.is_healthy,
        }

    def summary_lines(self) -> list[str]:
        """Human-readable lines, for the console runner and the soak report."""
        return [
            f"realtime factor  {self.real_time_factor:.2f}×  (must stay above 1.0)",
            f"commit latency   {self.engine.median_commit_latency:.2f} s median, "
            f"{self.engine.p95_commit_latency:.2f} s p95",
            f"queue            {self.queue.depth if self.queue else 0} deep, "
            f"{self.queue_fill:.0%} full",
            f"dropped audio    {self.dropped_frames} events (should be zero)",
            f"suppressed       {self.suppressed} passes of invented text",
            f"corrections      {self.engine.correction_rate:.0%} of passes",
            f"forced commits   {self.engine.forced_commit_rate:.0%} of commits",
        ]
