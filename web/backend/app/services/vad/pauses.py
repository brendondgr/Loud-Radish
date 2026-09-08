"""Where a finished recording goes quiet — the places it is safe to cut (D-061).

The detectors in this package answer one question per frame, and the gate above them turns those
answers into events while audio is still arriving. This module asks the same detectors a different
question about audio that has *stopped* arriving: over the whole file, where are the silences long
enough that cutting inside one cannot slice a word in half?

That is the third reason the interface docstring gives for a VAD existing at all, and until now
nothing used it. The batch pass cut on a clock and lost a word at every boundary; a dictation
transcribed through it came back with an ellipsis where the boundary had fallen mid-phrase.

**The gate's hysteresis is deliberately not used here.** It exists to stop a live gate flickering
at the edges of speech, and it enters late and leaves early on purpose. Over a finished file the
question is simpler — which frames are quiet — and a run of quiet frames shorter than the caller's
minimum is discarded anyway, which is all the debouncing this needs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..audio.formats import SAMPLE_RATE
from .base import VoiceActivityDetector

#: Silero consumes exactly 512 samples — 32 ms at 16 kHz — so a frame of that size runs the model
#: once per frame with nothing buffered, and the energy detector does not care.
FRAME_MS = 32


@dataclass(frozen=True)
class Pause:
    """A stretch of a recording in which nobody was speaking."""

    #: Seconds from the start of the recording.
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def middle_s(self) -> float:
        """The safest point to cut: as far from the last word as from the next."""
        return (self.start_s + self.end_s) / 2.0


def find_pauses(
    samples: np.ndarray,
    detector: VoiceActivityDetector,
    *,
    min_pause_ms: int = 300,
    frame_ms: int = FRAME_MS,
    sample_rate: int = SAMPLE_RATE,
) -> list[Pause]:
    """Every run of non-speech at least ``min_pause_ms`` long, in order.

    The detector is reset first, so an adaptive noise floor learns *this* recording rather than
    carrying over whatever it last heard. Leading and trailing silence count as pauses — a chunk
    planner is free to ignore them, and a cut inside either is as safe as any other.
    """
    array = np.asarray(samples, dtype=np.float32).ravel()
    if array.size == 0:
        return []

    frame = max(1, int(sample_rate * frame_ms / 1000))
    min_frames = max(1, int(round(min_pause_ms / frame_ms)))
    detector.reset()

    pauses: list[Pause] = []
    quiet_since: int | None = None
    total = array.size

    def close_run(end_sample: int) -> None:
        nonlocal quiet_since
        if quiet_since is None:
            return
        if (end_sample - quiet_since) >= min_frames * frame:
            pauses.append(Pause(start_s=quiet_since / sample_rate, end_s=end_sample / sample_rate))
        quiet_since = None

    for start in range(0, total, frame):
        chunk = array[start : start + frame]
        if detector.is_speech(chunk):
            close_run(start)
        elif quiet_since is None:
            quiet_since = start

    close_run(total)
    return pauses


__all__ = ["FRAME_MS", "Pause", "find_pauses"]
