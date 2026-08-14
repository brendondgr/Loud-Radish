"""Signal level measurement for the input meter (BE §4.1).

The meter answers one question the user asks constantly: *is audio actually arriving?* A flat line
means it is not, and that must be visible within a second of glancing at the screen — it is the
difference between "working, nobody is talking" and "broken".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: Samples at or above this magnitude are treated as clipped. Digital full scale is 1.0; real
#: converters distort slightly below it, so the threshold sits just under.
CLIPPING_THRESHOLD = 0.99

#: Floor for the dBFS conversion. True silence is negative infinity, which no meter can draw.
SILENCE_DBFS = -100.0


@dataclass(frozen=True)
class AudioLevel:
    """One measurement, matching the ``audio.level`` transport event."""

    rms: float
    peak: float
    clipping: bool

    @property
    def rms_dbfs(self) -> float:
        """RMS in decibels relative to full scale."""
        return to_dbfs(self.rms)

    @property
    def peak_dbfs(self) -> float:
        """Peak in decibels relative to full scale."""
        return to_dbfs(self.peak)

    def as_event(self) -> dict[str, float | bool]:
        """The JSON-safe payload for the ``audio.level`` event."""
        return {
            "rms": round(self.rms, 5),
            "peak": round(self.peak, 5),
            "clipping": self.clipping,
            "rms_dbfs": round(self.rms_dbfs, 2),
        }


SILENT = AudioLevel(rms=0.0, peak=0.0, clipping=False)


def to_dbfs(amplitude: float) -> float:
    """Convert a 0.0–1.0 amplitude to dBFS, floored at :data:`SILENCE_DBFS`."""
    if amplitude <= 0.0:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20.0 * math.log10(min(1.0, amplitude)))


def measure(samples: np.ndarray) -> AudioLevel:
    """Measure RMS, peak, and clipping over one frame.

    An empty frame reports silence rather than raising: the meter is polled on a timer and must
    tolerate arriving between frames.
    """
    array = np.asarray(samples, dtype=np.float32).ravel()
    if array.size == 0:
        return SILENT

    peak = float(np.max(np.abs(array)))
    rms = float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))
    return AudioLevel(rms=rms, peak=peak, clipping=peak >= CLIPPING_THRESHOLD)


class LevelMeter:
    """Smooths successive measurements for display.

    Raw frame-to-frame RMS jitters enough to be visually noisy over a two-hour session. The meter
    applies asymmetric smoothing — fast attack so speech onset is visible immediately, slow release
    so the bar does not flicker between syllables.
    """

    def __init__(self, attack: float = 0.6, release: float = 0.12) -> None:
        if not 0.0 < attack <= 1.0 or not 0.0 < release <= 1.0:
            raise ValueError("Attack and release coefficients must be in (0, 1]")
        self._attack = attack
        self._release = release
        self._rms = 0.0
        self._peak = 0.0

    def update(self, samples: np.ndarray) -> AudioLevel:
        """Measure a frame and return the smoothed level."""
        raw = measure(samples)
        self._rms = self._smooth(self._rms, raw.rms)
        self._peak = self._smooth(self._peak, raw.peak)
        return AudioLevel(rms=self._rms, peak=self._peak, clipping=raw.clipping)

    def _smooth(self, current: float, target: float) -> float:
        coefficient = self._attack if target > current else self._release
        return current + coefficient * (target - current)

    def reset(self) -> None:
        """Drop to silence — used when the device changes or a session starts."""
        self._rms = 0.0
        self._peak = 0.0
