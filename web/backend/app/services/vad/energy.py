"""An energy-based voice activity detector with an adaptive noise floor.

The dependency-free default. It is not as good as a learned detector at separating speech from
structured noise, but it is good enough for the job the VAD actually does here — gating an expensive
model and marking pauses — and it costs nothing to install or to run.

Two ideas do most of the work:

* **An adaptive noise floor.** A fixed threshold works in one room and fails in the next. The floor
  tracks the quietest recent frames, so a hall with loud ventilation and a quiet seminar room both
  end up with a sensible margin above their own background.
* **A zero-crossing check.** Steady tones and hum can be as loud as speech but have a very different
  zero-crossing rate. Requiring the rate to fall in a speech-like band rejects the obvious
  impostors without rejecting quiet speech.
"""

from __future__ import annotations

import numpy as np

from .base import VoiceActivityDetector

#: How quickly the noise floor tracks quiet frames. Fast enough to reach the trough between
#: syllables — roughly eight frames at a 4 Hz syllable rate — but not so fast that one quiet frame
#: resets it. The decay upward is far slower, so a burst of speech does not desensitise the
#: detector for the rest of the talk.
FLOOR_ATTACK = 0.15
FLOOR_DECAY = 0.002

#: Sensitivity 0.0–1.0 maps onto this margin above the noise floor, in linear amplitude ratio.
#: Most sensitive picks up speech only just above the background; least sensitive demands much more.
#:
#: Calibrated against the fixture generator rather than guessed. The floor settles near the trough
#: between syllables, not at true silence, so the usable ratio for speech is a few times over —
#: not the order of magnitude a peak-versus-silence comparison would suggest. A margin set from
#: that intuition rejects continuous speech outright, which is the worst possible failure: the
#: transcript simply stops with no indication why.
MARGIN_AT_MAX_SENSITIVITY = 1.3
MARGIN_AT_MIN_SENSITIVITY = 4.0

#: Absolute floor. Below this, even a large ratio above the noise floor is not speech — it is a
#: silent room being divided by an even quieter number.
ABSOLUTE_FLOOR_RMS = 0.0008

#: Zero-crossing rate band that speech occupies. Below it is hum and rumble; above it is hiss.
#: A pure tone crosses zero at twice its frequency, so the lower bound of 0.02 rejects anything
#: whose energy sits below roughly 160 Hz — mains hum, ventilation, handling noise — while voiced
#: speech, whose formants push the composite rate well above that, passes comfortably.
MIN_ZERO_CROSSING_RATE = 0.02
MAX_ZERO_CROSSING_RATE = 0.45


class EnergyVad(VoiceActivityDetector):
    """Detects speech by RMS energy relative to an adaptive noise floor."""

    def __init__(self, sensitivity: float = 0.6) -> None:
        self._margin = _margin_for(sensitivity)
        self._sensitivity = sensitivity
        self._floor = ABSOLUTE_FLOOR_RMS
        self._initialised = False

    @property
    def name(self) -> str:
        """Short identifier for status output."""
        return "energy"

    @property
    def noise_floor(self) -> float:
        """The current adaptive noise floor, for diagnostics."""
        return self._floor

    def set_sensitivity(self, sensitivity: float) -> None:
        """Adjust how far above the noise floor a frame must sit to count as speech."""
        if not 0.0 <= sensitivity <= 1.0:
            raise ValueError(f"Sensitivity must be between 0.0 and 1.0, got {sensitivity}")
        self._sensitivity = sensitivity
        self._margin = _margin_for(sensitivity)

    def reset(self) -> None:
        """Forget the learned noise floor."""
        self._floor = ABSOLUTE_FLOOR_RMS
        self._initialised = False

    def is_speech(self, frame: np.ndarray) -> bool:
        """Whether this frame is loud enough, and spectrally plausible enough, to be speech."""
        array = np.asarray(frame, dtype=np.float32).ravel()
        if array.size == 0:
            return False

        rms = float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))
        threshold = self._update_floor(rms)

        if rms < max(threshold, ABSOLUTE_FLOOR_RMS):
            return False
        return _plausible_zero_crossing_rate(array)

    def score(self, frame: np.ndarray) -> float:
        """How far above the speech threshold this frame sits, clamped to 0.0–1.0."""
        array = np.asarray(frame, dtype=np.float32).ravel()
        if array.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))
        threshold = max(self._floor * self._margin, ABSOLUTE_FLOOR_RMS)
        return float(min(1.0, rms / threshold)) if threshold > 0 else 0.0

    def _update_floor(self, rms: float) -> float:
        """Track the noise floor and return the current speech threshold.

        The floor rises slowly and falls quickly, so a sustained increase in background noise is
        eventually learned while a burst of speech does not permanently desensitise the detector.
        """
        if not self._initialised:
            self._floor = max(rms, ABSOLUTE_FLOOR_RMS)
            self._initialised = True
        elif rms < self._floor:
            self._floor += FLOOR_ATTACK * (rms - self._floor)
        else:
            self._floor += FLOOR_DECAY * (rms - self._floor)

        self._floor = max(self._floor, ABSOLUTE_FLOOR_RMS * 0.5)
        return self._floor * self._margin


def _margin_for(sensitivity: float) -> float:
    """Map a 0.0–1.0 sensitivity onto a threshold margin above the noise floor."""
    sensitivity = min(1.0, max(0.0, sensitivity))
    span = MARGIN_AT_MIN_SENSITIVITY - MARGIN_AT_MAX_SENSITIVITY
    return MARGIN_AT_MIN_SENSITIVITY - span * sensitivity


def _plausible_zero_crossing_rate(frame: np.ndarray) -> bool:
    """Whether the frame's zero-crossing rate falls in the band speech occupies."""
    if frame.size < 2:
        return False
    crossings = int(np.count_nonzero(np.diff(np.signbit(frame))))
    rate = crossings / (frame.size - 1)
    return MIN_ZERO_CROSSING_RATE <= rate <= MAX_ZERO_CROSSING_RATE
