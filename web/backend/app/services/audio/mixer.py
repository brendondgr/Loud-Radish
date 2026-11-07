"""The arithmetic of mixing several audio inputs into one (D-081).

Pure: frames in, frames out, no threads and no devices. :class:`~.sources.mixed.MixedSource` owns
the timing — which input drives the clock, how far another may drift — and calls these for the
sums, so the part that can be wrong by a sample is checkable without anything running.

Volumes are whole percentages, 0 to 100, because that is what a person sets on a slider and what the
API carries. 100 % is the input exactly as captured; there is no boost (a quiet microphone is a
follow-up, not a slider that can clip).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .formats import DTYPE

#: Loudest a volume may be set. The input as captured.
MAX_VOLUME = 100


def percent_to_gain(volume: int | float) -> float:
    """A 0–100 volume as a linear gain, clamped rather than refused."""
    return max(0.0, min(float(volume), MAX_VOLUME)) / MAX_VOLUME


@dataclass(frozen=True)
class InputLevel:
    """What one input is set to: a volume, and whether it is muted.

    **Mute is not volume 0, though it sounds the same.** It keeps the volume, so unmuting puts the
    slider back where it was rather than at nothing — which is what a mute button means.
    """

    volume: int = MAX_VOLUME
    muted: bool = False

    @property
    def gain(self) -> float:
        """The gain this setting applies. Zero when muted, whatever the volume."""
        return 0.0 if self.muted else percent_to_gain(self.volume)

    def as_dict(self) -> dict[str, int | bool]:
        return {"volume": int(self.volume), "muted": self.muted}


def apply_gain(frame: np.ndarray, start: float, end: float) -> np.ndarray:
    """``frame`` scaled by a gain moving from ``start`` to ``end`` across it.

    **Ramped, never stepped.** A gain that jumps between two samples is a discontinuity in the
    waveform, and a discontinuity is a click — audible on every drag of a slider. Spreading the
    change across one frame (32 ms) is too short to hear as a fade and long enough to remove the
    click.

    A frame at unity gain is returned unchanged — the same array, not a copy — so an input at 100 %
    passes through a mixer sample for sample.
    """
    if start == end:
        if start == 1.0:
            return frame
        if start == 0.0:
            return np.zeros_like(frame)
        return (frame * np.float32(start)).astype(DTYPE, copy=False)
    ramp = np.linspace(start, end, frame.size, endpoint=False, dtype=np.float32)
    return (frame * ramp).astype(DTYPE, copy=False)


def mix(frames: list[np.ndarray]) -> np.ndarray:
    """Sum frames of equal length and keep the result inside [-1, 1].

    One frame is returned as it is: nothing was summed, so there is nothing to clip, and a
    single-input recording must not be altered by passing through here.

    **Clipped, not normalised.** Dividing by the number of inputs would halve a narrator's voice the
    moment a window's sound was added, whether or not the window was making any; a hard clip only
    touches the samples that actually overflow, which two sources at sensible levels rarely do.
    """
    if len(frames) == 1:
        return frames[0]
    total = np.sum(frames, axis=0, dtype=np.float32)
    return np.clip(total, -1.0, 1.0, out=total).astype(DTYPE, copy=False)


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of a frame, for a meter. Zero for an empty frame."""
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
