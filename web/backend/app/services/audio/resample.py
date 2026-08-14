"""Sample-rate conversion (BE §4.2).

A soundcard typically produces 44 100 or 48 000 Hz; speech models expect 16 000. Naive decimation —
keeping every third sample — introduces aliasing that degrades recognition in a way that looks
like a model problem and is not. ``soxr`` does the band-limited conversion properly and is the
only path used in practice.

The linear fallback exists so this package imports in an environment where ``soxr`` failed to build.
It warns loudly, once, because audio that has been through it will transcribe measurably worse.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_FALLBACK_WARNED = False


def _soxr():
    """Return the ``soxr`` module, or ``None`` if it is unavailable."""
    try:
        import soxr
    except ImportError:
        return None
    return soxr


def resampler_name() -> str:
    """Which resampler is in use. Surfaced in health output rather than discovered by ear."""
    return "soxr" if _soxr() is not None else "linear-fallback"


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Convert ``samples`` from ``source_rate`` to ``target_rate``.

    Returns the input unchanged when the rates already match, which is the common case for the file
    source and for devices that can open at 16 kHz directly.
    """
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError(f"Sample rates must be positive, got {source_rate} → {target_rate}")

    array = np.ascontiguousarray(samples, dtype=np.float32)
    if source_rate == target_rate or array.size == 0:
        return array

    soxr = _soxr()
    if soxr is not None:
        converted = soxr.resample(array, source_rate, target_rate, quality="HQ")
        return np.ascontiguousarray(converted, dtype=np.float32)

    return _linear_resample(array, source_rate, target_rate)


def _linear_resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Last-resort linear interpolation. Aliases on downsampling; see the module docstring."""
    global _FALLBACK_WARNED
    if not _FALLBACK_WARNED:
        logger.warning(
            "soxr is unavailable; falling back to linear resampling. Recognition accuracy will be "
            "measurably worse. Reinstall dependencies with `uv sync` to restore proper resampling."
        )
        _FALLBACK_WARNED = True

    ratio = target_rate / source_rate
    out_length = max(1, int(round(samples.size * ratio)))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.arange(out_length, dtype=np.float64) / ratio
    converted = np.interp(target_positions, source_positions, samples.astype(np.float64))
    return np.ascontiguousarray(converted, dtype=np.float32)
