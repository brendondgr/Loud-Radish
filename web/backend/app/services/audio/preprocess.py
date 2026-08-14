"""Optional preprocessing, deliberately minimal (BE §4.5).

Each stage is individually toggleable because each can hurt as easily as help:

* **Gain normalisation** helps a quiet room and amplifies noise during silence.
* **High-pass filtering** at ~80 Hz removes HVAC rumble and handling noise. Cheap, generally safe.
* **Noise suppression** is deliberately absent. Aggressive suppression damages speech and costs more
  accuracy than the noise did.

Both stages are stateful across frames — a filter reset every 32 ms would produce a click at every
frame boundary — so the chain is an object, not a function.
"""

from __future__ import annotations

import math

import numpy as np

from ...config.schema import AudioConfig
from .formats import DTYPE, SAMPLE_RATE, clip_to_range

#: Target RMS for gain normalisation, roughly −20 dBFS. Comfortable for speech models without
#: pushing transients into clipping.
TARGET_RMS = 0.1

#: Below this input RMS the frame is treated as silence and left alone. Without it, normalisation
#: turns room tone into a full-scale hiss during every pause — precisely when the ASR is most prone
#: to hallucinating.
NOISE_FLOOR_RMS = 0.002

#: Ceiling on applied gain, in linear terms (~26 dB).
MAX_GAIN = 20.0


class HighPassFilter:
    """A one-pole high-pass filter, carrying state across frames.

    One pole is a gentle 6 dB/octave slope. That is intentional: the goal is removing rumble well
    below the speech band, not shaping the speech itself.
    """

    def __init__(self, cutoff_hz: float = 80.0, sample_rate: int = SAMPLE_RATE) -> None:
        if not 0 < cutoff_hz < sample_rate / 2:
            raise ValueError(f"Cutoff {cutoff_hz} Hz must be between 0 and Nyquist")
        self._alpha = self._coefficient(cutoff_hz, sample_rate)
        self._prev_in = 0.0
        self._prev_out = 0.0

    @staticmethod
    def _coefficient(cutoff_hz: float, sample_rate: int) -> float:
        rc = 1.0 / (2.0 * math.pi * cutoff_hz)
        dt = 1.0 / sample_rate
        return rc / (rc + dt)

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Filter one frame, continuing from the previous frame's state.

        The recurrence is inherently sequential, so this is a Python loop over a plain list —
        which is several times faster than indexing a NumPy array element by element, and at
        16 000 iterations per second of audio costs a fraction of a percent of real time.
        """
        array = np.asarray(samples, dtype=np.float32).ravel()
        if array.size == 0:
            return array

        alpha = self._alpha
        prev_in = self._prev_in
        prev_out = self._prev_out
        out: list[float] = []
        append = out.append
        for sample in array.tolist():
            prev_out = alpha * (prev_out + sample - prev_in)
            prev_in = sample
            append(prev_out)
        self._prev_in = prev_in
        self._prev_out = prev_out
        return np.asarray(out, dtype=np.float32)

    def reset(self) -> None:
        """Clear filter state — on device change, so the new stream does not inherit a transient."""
        self._prev_in = 0.0
        self._prev_out = 0.0


class GainNormaliser:
    """Brings frame level towards a target RMS, smoothly.

    The applied gain is smoothed across frames. An instantaneous per-frame correction produces
    audible pumping, and a model trained on natural speech dynamics does worse on pumped audio than
    on quiet audio.
    """

    def __init__(self, target_rms: float = TARGET_RMS, smoothing: float = 0.15) -> None:
        self._target = target_rms
        self._smoothing = smoothing
        self._gain = 1.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Apply smoothed gain to one frame."""
        array = np.asarray(samples, dtype=np.float32).ravel()
        if array.size == 0:
            return array

        rms = float(np.sqrt(np.mean(np.square(array, dtype=np.float64))))
        if rms >= NOISE_FLOOR_RMS:
            desired = min(MAX_GAIN, self._target / rms)
            self._gain += self._smoothing * (desired - self._gain)

        return clip_to_range(array * np.float32(self._gain))

    @property
    def gain(self) -> float:
        """The gain currently being applied, for diagnostics."""
        return self._gain

    def reset(self) -> None:
        """Return to unity gain."""
        self._gain = 1.0


class PreprocessChain:
    """The configured preprocessing stages, applied in a fixed order.

    Order matters: the high-pass filter runs first so that rumble does not inflate the RMS the gain
    stage measures. Reversing them would have low-frequency noise suppress the gain applied to
    speech.
    """

    def __init__(self, config: AudioConfig, sample_rate: int = SAMPLE_RATE) -> None:
        self._config = config
        self._high_pass = HighPassFilter(config.high_pass_hz, sample_rate)
        self._normaliser = GainNormaliser()
        self._static_gain = np.float32(10.0 ** (config.gain_db / 20.0))

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Run one frame through the enabled stages."""
        out = np.ascontiguousarray(samples, dtype=DTYPE).ravel()
        if out.size == 0:
            return out

        if self._config.high_pass:
            out = self._high_pass.process(out)
        if self._config.gain_db != 0.0:
            out = clip_to_range(out * self._static_gain)
        if self._config.normalise_gain:
            out = self._normaliser.process(out)
        return out

    def update_config(self, config: AudioConfig) -> None:
        """Adopt new settings without dropping filter state.

        Gain and toggles are live settings (see ``config/hotswap.py``), so they change mid-session.
        Only a changed cutoff frequency rebuilds the filter, because only that changes its shape.
        """
        if config.high_pass_hz != self._config.high_pass_hz:
            self._high_pass = HighPassFilter(config.high_pass_hz)
        self._static_gain = np.float32(10.0 ** (config.gain_db / 20.0))
        self._config = config

    def reset(self) -> None:
        """Clear all stage state."""
        self._high_pass.reset()
        self._normaliser.reset()
