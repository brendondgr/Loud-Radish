"""The Silero voice activity detector, behind the same interface.

A small learned model, substantially better than energy thresholding at separating speech from
structured noise — applause, door slams, chair scrapes, a projector fan — which is exactly the noise
a lecture hall produces.

It needs the optional ``vad-silero`` dependency group and a model file. When either is missing the
constructor says so and names the install command, rather than failing later with an import error
from inside the capture thread.

.. note::
   Silero consumes a fixed window of 512 samples at 16 kHz. Frames of any other size are buffered
   and consumed in whole windows, so the frame duration configured for capture stays independent of
   the model's requirement.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..audio.formats import SAMPLE_RATE
from .base import VoiceActivityDetector

logger = logging.getLogger(__name__)

#: Silero's expected input window at 16 kHz.
WINDOW_SAMPLES = 512

#: Sensitivity 0.0–1.0 maps onto this probability threshold, inverted: most sensitive accepts a low
#: probability, least sensitive demands a high one.
THRESHOLD_AT_MAX_SENSITIVITY = 0.25
THRESHOLD_AT_MIN_SENSITIVITY = 0.8


class SileroUnavailableError(RuntimeError):
    """Raised when the Silero detector cannot be constructed."""


class SileroVad(VoiceActivityDetector):
    """Wraps the Silero ONNX model as a :class:`VoiceActivityDetector`."""

    def __init__(
        self,
        model_path: str | Path,
        sensitivity: float = 0.6,
        session: Any | None = None,
    ) -> None:
        self._threshold = _threshold_for(sensitivity)
        self._path = Path(model_path)
        self._session = session or self._build_session()
        self._pending = np.zeros(0, dtype=np.float32)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._last_score = 0.0

    @property
    def name(self) -> str:
        """Short identifier for status output."""
        return "silero"

    def set_sensitivity(self, sensitivity: float) -> None:
        """Adjust the speech-probability threshold."""
        if not 0.0 <= sensitivity <= 1.0:
            raise ValueError(f"Sensitivity must be between 0.0 and 1.0, got {sensitivity}")
        self._threshold = _threshold_for(sensitivity)

    def reset(self) -> None:
        """Clear the model's recurrent state and any buffered partial window."""
        self._pending = np.zeros(0, dtype=np.float32)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._last_score = 0.0

    def is_speech(self, frame: np.ndarray) -> bool:
        """Whether the model's speech probability for this frame exceeds the threshold."""
        return self.score(frame) >= self._threshold

    def score(self, frame: np.ndarray) -> float:
        """Run the model over whole windows and return the highest probability seen.

        Taking the maximum rather than the mean matters: a frame spanning the boundary between
        silence and speech should read as speech, so the gate enters on time.
        """
        array = np.asarray(frame, dtype=np.float32).ravel()
        if array.size == 0:
            return self._last_score

        pending = np.concatenate([self._pending, array]) if self._pending.size else array
        count = (pending.size // WINDOW_SAMPLES) * WINDOW_SAMPLES
        self._pending = pending[count:].copy()

        best = 0.0
        for start in range(0, count, WINDOW_SAMPLES):
            best = max(best, self._infer(pending[start : start + WINDOW_SAMPLES]))

        if count > 0:
            self._last_score = best
        return self._last_score

    def _infer(self, window: np.ndarray) -> float:
        """Run one window through the model."""
        inputs = {
            "input": window.reshape(1, -1),
            "state": self._state,
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        }
        output, self._state = self._session.run(None, inputs)
        return float(np.asarray(output).ravel()[0])

    def _build_session(self) -> Any:
        """Create the ONNX inference session, or explain precisely what is missing."""
        try:
            import onnxruntime
        except ImportError as exc:
            raise SileroUnavailableError(
                "The Silero detector needs the optional VAD backend. "
                "Install it with: uv sync --extra vad-silero — or switch vad.detector to 'energy', "
                "which needs nothing."
            ) from exc

        if not self._path.is_file():
            raise SileroUnavailableError(
                f"No Silero model at {self._path}. Download silero_vad.onnx and point "
                "vad.model_path at it, or switch vad.detector to 'energy'."
            )

        options = onnxruntime.SessionOptions()
        # One thread: the VAD is tiny, and extra threads only contend with ASR inference.
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        return onnxruntime.InferenceSession(str(self._path), sess_options=options)


def _threshold_for(sensitivity: float) -> float:
    """Map a 0.0–1.0 sensitivity onto a speech-probability threshold."""
    sensitivity = min(1.0, max(0.0, sensitivity))
    span = THRESHOLD_AT_MIN_SENSITIVITY - THRESHOLD_AT_MAX_SENSITIVITY
    return THRESHOLD_AT_MIN_SENSITIVITY - span * sensitivity
