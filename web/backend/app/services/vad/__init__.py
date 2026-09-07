"""Voice activity detection (BE §5).

* ``base``       — the one-question-per-frame detector interface
* ``energy``     — the dependency-free default, with an adaptive noise floor
* ``silero``     — an optional learned detector behind the same interface
* ``hysteresis`` — shared debouncing and pause-event emission, above the interface

The split matters: detectors answer only "is this frame speech?", and everything that is easy to
get subtly wrong — flicker, entering late, leaving early, when a pause counts as a pause — is
implemented exactly once in :class:`SpeechGate`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ...config.schema import VadConfig
from ..audio.formats import SAMPLE_RATE
from .base import VoiceActivityDetector
from .energy import EnergyVad
from .hysteresis import SpeechGate, SpeechState, VadFrameResult
from .silero import SileroUnavailableError, SileroVad

logger = logging.getLogger(__name__)


def build_detector(
    config: VadConfig, on_fallback: Callable[[str], None] | None = None
) -> VoiceActivityDetector:
    """Construct the configured detector.

    Falls back to the energy detector when Silero is selected but unavailable — losing detector
    quality is far better than losing the transcript.

    **But the fallback is announced.** It used to be a `logger.warning` and nothing else, and the
    result was an application that reported one detector in its status and ran another for months,
    on the developer's own machine, without anyone noticing. `on_fallback` is how the session
    manager turns that into something the user can see.
    """
    if config.detector == "silero":
        try:
            return SileroVad(
                model_path=config.model_path or "silero_vad.onnx",
                sensitivity=config.sensitivity,
            )
        except SileroUnavailableError as exc:
            logger.warning("Falling back to the energy detector: %s", exc)
            if on_fallback is not None:
                on_fallback(str(exc))

    return EnergyVad(sensitivity=config.sensitivity)


def build_gate(
    config: VadConfig,
    frame_ms: int = 32,
    sample_rate: int = SAMPLE_RATE,
    detector: VoiceActivityDetector | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> SpeechGate:
    """Construct a debounced speech gate from configuration."""
    return SpeechGate(
        detector=detector or build_detector(config, on_fallback),
        config=config,
        frame_ms=frame_ms,
        sample_rate=sample_rate,
    )


__all__ = [
    "EnergyVad",
    "SileroUnavailableError",
    "SileroVad",
    "SpeechGate",
    "SpeechState",
    "VadFrameResult",
    "VoiceActivityDetector",
    "build_detector",
    "build_gate",
]
