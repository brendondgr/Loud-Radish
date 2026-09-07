"""The Silero voice activity detector, behind the same interface.

A small learned model, substantially better than energy thresholding at separating speech from
structured noise — applause, door slams, chair scrapes, a projector fan — which is exactly the noise
a lecture hall produces.

**It needed a model file nobody was told to fetch, and so it never ran.** The configuration on the
developer's own machine asked for Silero; `silero_vad.onnx` was not present anywhere; the factory
caught the resulting error and fell back to the energy detector with a `logger.warning` nobody
reads. The application reported one detector and used another for months. Worse, the message it
raised named an optional ``vad-silero`` dependency group that **D-023 had already removed**, so
following its instructions could not possibly have helped.

There is no download. `faster-whisper` already carries this model for the decoder's own speech
filter, so when no path is configured the bundled copy is used — which means a machine that can
transcribe at all can run Silero. See :func:`bundled_model`.

.. note::
   Silero consumes a fixed window of 512 samples at 16 kHz. Frames of any other size are buffered
   and consumed in whole windows, so the frame duration configured for capture stays independent of
   the model's requirement.

.. note::
   Two generations of the model are in circulation and their signatures differ; both are supported
   and the right one is detected from the session. See :meth:`SileroVad._infer`.
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

#: v6 prepends 64 samples of the previous window to each one it scores. v5 does not.
CONTEXT_SAMPLES = 64

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
        self._api = _api_of(self._session)
        self._pending = np.zeros(0, dtype=np.float32)
        self._last_score = 0.0
        self._reset_state()

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
        self._last_score = 0.0
        self._reset_state()

    def _reset_state(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)

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
        """Run one window through the model, in whichever dialect it speaks.

        **Two generations of the model exist and they do not agree.** v5 takes
        ``(input, state, sr)`` with one combined `(2, 1, 128)` state and a bare 512-sample window.
        v6 — which is the one bundled with `faster-whisper`, and therefore the one most likely to
        be present — takes ``(input, h, c)`` with separate LSTM states and a **576**-sample row:
        64 samples of context from the previous window followed by the 512 of this one.

        Detected from the session rather than configured, because the file a user points at is the
        one thing this cannot know in advance.
        """
        if self._api == "v6":
            row = np.concatenate([self._context, window]).reshape(1, -1)
            output, self._h, self._c = self._session.run(
                None, {"input": row, "h": self._h, "c": self._c}
            )
            self._context = window[-CONTEXT_SAMPLES:].astype(np.float32, copy=True)
            return float(np.asarray(output).ravel()[-1])

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
                "The Silero detector needs `onnxruntime`, which is a core dependency of this "
                "project and should already be installed. Reinstall with `uv sync`, or switch "
                "vad.detector to 'energy', which needs nothing."
            ) from exc

        path = self._path if self._path.is_file() else bundled_model()
        if path is None:
            raise SileroUnavailableError(
                f"No Silero model at {self._path}, and none is bundled with the installed "
                "faster-whisper. Point vad.model_path at a silero_vad.onnx, or switch "
                "vad.detector to 'energy'."
            )
        self._path = path

        options = onnxruntime.SessionOptions()
        # One thread: the VAD is tiny, and extra threads only contend with ASR inference.
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        return onnxruntime.InferenceSession(str(path), sess_options=options)


def _api_of(session: Any) -> str:
    """Which generation of the model this session is: ``v5`` or ``v6``."""
    try:
        names = {entry.name for entry in session.get_inputs()}
    except Exception:  # noqa: BLE001 - a hand-made double in a test
        return "v5"
    return "v6" if {"h", "c"} <= names else "v5"


def bundled_model() -> Path | None:
    """The Silero model that ships inside `faster-whisper`, if it is installed.

    **This is why nothing has to be downloaded.** `faster-whisper` carries a copy for the decoder's
    own speech filter, and it is the same model — so a configuration asking for Silero can be
    honoured on any machine that can transcribe at all, rather than silently becoming the energy
    detector because a file nobody was told to fetch is missing.
    """
    try:
        from faster_whisper.utils import get_assets_path
    except ImportError:
        return None
    for name in ("silero_vad_v6.onnx", "silero_vad.onnx"):
        candidate = Path(get_assets_path()) / name
        if candidate.is_file():
            return candidate
    return None


def _threshold_for(sensitivity: float) -> float:
    """Map a 0.0–1.0 sensitivity onto a speech-probability threshold."""
    sensitivity = min(1.0, max(0.0, sensitivity))
    span = THRESHOLD_AT_MIN_SENSITIVITY - THRESHOLD_AT_MAX_SENSITIVITY
    return THRESHOLD_AT_MIN_SENSITIVITY - span * sensitivity
