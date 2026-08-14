"""The canonical audio format, and conversion into it (BE §4.2).

Every component downstream of capture assumes exactly this format. It is enforced here — at the
capture boundary — and nowhere else. A stage that re-checks the format is a stage that has
learned to distrust this module, which is worse than the check is worth.

.. list-table::
   :header-rows: 1

   * - Property
     - Value
   * - Sample rate
     - 16 000 Hz
   * - Channels
     - 1 (mono, stereo downmixed by averaging)
   * - Sample type
     - 32-bit float
   * - Range
     - −1.0 to +1.0
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = np.float32

#: Frame durations outside this range either thrash the queue or make the VAD sluggish.
MIN_FRAME_MS = 10
MAX_FRAME_MS = 200


class AudioFormatError(ValueError):
    """Raised when audio cannot be converted into the canonical format."""


def frame_samples(frame_ms: int, sample_rate: int = SAMPLE_RATE) -> int:
    """Return the sample count for a frame of ``frame_ms`` milliseconds."""
    if not MIN_FRAME_MS <= frame_ms <= MAX_FRAME_MS:
        raise AudioFormatError(
            f"Frame duration {frame_ms} ms is outside the supported "
            f"{MIN_FRAME_MS}–{MAX_FRAME_MS} ms range."
        )
    return int(round(sample_rate * frame_ms / 1000.0))


def duration_seconds(samples: int, sample_rate: int = SAMPLE_RATE) -> float:
    """Convert a sample count to seconds."""
    return samples / float(sample_rate)


def to_float32(samples: np.ndarray) -> np.ndarray:
    """Convert any common sample dtype to float32 normalised to −1.0…+1.0.

    Integer input is divided by the maximum magnitude of its own dtype. Dividing by the wrong
    constant is a quiet bug: audio still plays, just at the wrong level, which then trips gain
    normalisation and the VAD threshold in ways that look like a model problem.
    """
    array = np.asarray(samples)

    if array.dtype == np.float32:
        return array
    if array.dtype in (np.float64, np.float16):
        return array.astype(np.float32)
    if np.issubdtype(array.dtype, np.integer):
        info = np.iinfo(array.dtype)
        if info.min < 0:
            # Signed: scale by the larger magnitude so the result cannot exceed 1.0.
            return (array.astype(np.float32) / float(max(info.max, -info.min))).astype(np.float32)
        # Unsigned: centre on zero first.
        midpoint = (info.max + 1) / 2.0
        return ((array.astype(np.float32) - midpoint) / midpoint).astype(np.float32)

    raise AudioFormatError(f"Unsupported sample dtype {array.dtype!r}")


def downmix(samples: np.ndarray) -> np.ndarray:
    """Reduce multi-channel audio to mono by averaging channels.

    Accepts either ``(frames, channels)`` or a flat interleaved array reshaped by the caller.
    Already-mono input passes through untouched.
    """
    array = np.asarray(samples)
    if array.ndim == 1:
        return array
    if array.ndim != 2:
        raise AudioFormatError(f"Expected 1-D or 2-D audio, got {array.ndim} dimensions")
    if array.shape[1] == 1:
        return array[:, 0]
    return array.mean(axis=1, dtype=np.float32)


def to_canonical(
    samples: np.ndarray,
    source_rate: int,
    *,
    channels: int | None = None,
) -> np.ndarray:
    """Convert arbitrary device audio into the canonical format.

    Args:
        samples: raw samples, mono or ``(frames, channels)``.
        source_rate: the rate ``samples`` was captured at.
        channels: channel count, when ``samples`` is flat and interleaved.

    Returns:
        A contiguous 1-D float32 array at 16 kHz, normalised to −1.0…+1.0.
    """
    from .resample import resample

    array = np.asarray(samples)
    if channels is not None and channels > 1 and array.ndim == 1:
        if array.size % channels:
            raise AudioFormatError(
                f"Interleaved buffer of {array.size} samples is not divisible by "
                f"{channels} channels"
            )
        array = array.reshape(-1, channels)

    mono = downmix(to_float32(array))
    resampled = resample(mono, source_rate, SAMPLE_RATE)
    return np.ascontiguousarray(resampled, dtype=DTYPE)


def is_canonical(samples: np.ndarray) -> bool:
    """Whether ``samples`` already satisfies the canonical shape and dtype.

    Used by assertions in tests rather than as a runtime gate — see the module docstring.
    """
    array = np.asarray(samples)
    return array.ndim == 1 and array.dtype == np.float32


def clip_to_range(samples: np.ndarray) -> np.ndarray:
    """Clamp samples into −1.0…+1.0.

    Applied after any stage that can add gain. Values outside the range are not merely loud: they
    wrap or saturate unpredictably once handed to a model's own preprocessing.
    """
    return np.clip(samples, -1.0, 1.0, dtype=np.float32, casting="unsafe")
