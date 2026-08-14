"""The `faster-whisper` backend — a CTranslate2 reimplementation of Whisper.

The recommended default for real use (BE §6.4, Family 1). Autoregressive encoder-decoder: broad
language coverage, deep ecosystem, well-understood quirks. Slower than a transducer, and prone to
inventing text when fed silence — which is why the streaming engine's silence gate exists.

The import is deferred to :meth:`load` so this module is importable, and the backend enumerable, in
an environment where the optional ``asr-whisper`` group is not installed. Discovering a missing
dependency at import time would mean the whole ASR package fails to import, taking the mock backend
down with it.

.. warning::
   **Real-model transcription cannot be verified in this repository's test environment.** The
   optional dependency and its model weights are not installed, so coverage here is limited to
   interface conformance, the import guard, and the conversion from `faster-whisper`'s output shape
   into :class:`WordToken`. Measuring real-time factor on actual hardware is a manual step, tracked
   in ``docs/checklist.md`` Part 4.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from ..audio.formats import SAMPLE_RATE
from .contract import AsrBackend, AsrCapabilities, AsrLoadError, AsrResult, WordToken

logger = logging.getLogger(__name__)

#: Whisper's hard window. Submitting more is silently truncated by the model, which produces a
#: transcript missing its tail rather than an error — so the engine must respect this.
MAX_AUDIO_SECONDS = 30.0

#: Languages Whisper handles well enough to offer. ``*`` because coverage is genuinely broad and
#: enumerating ninety-nine codes in the UI helps nobody.
LANGUAGES = ["*"]


class FasterWhisperBackend(AsrBackend):
    """Drives a `faster-whisper` model behind the ASR interface."""

    def __init__(
        self,
        model: str = "small",
        device: str = "auto",
        precision: str = "int8",
        language: str | None = "en",
        beam_size: int = 1,
        model_factory: Any | None = None,
    ) -> None:
        self._model_name = model
        self._device = device
        self._precision = precision
        self._language = language
        self._beam_size = beam_size
        self._model_factory = model_factory
        self._model: Any | None = None

    # -- identity ------------------------------------------------------------------

    @property
    def backend_id(self) -> str:
        """Stable identifier used in configuration."""
        return "faster-whisper"

    @property
    def model_id(self) -> str:
        """Identifier recorded on segments this backend produces."""
        return f"faster-whisper:{self._model_name}:{self._precision}"

    @property
    def capabilities(self) -> AsrCapabilities:
        """What this backend can do.

        ``streaming_native`` is false: Whisper is trained on complete 30-second segments, not
        streams, so it needs the full commit machinery of the streaming engine.
        """
        return AsrCapabilities(
            word_timestamps=True,
            streaming_native=False,
            accepts_prompt=True,
            languages=LANGUAGES,
            max_audio_seconds=MAX_AUDIO_SECONDS,
            runs_on="cuda" if self._resolved_device() == "cuda" else "cpu",
            confidence=True,
        )

    @property
    def is_loaded(self) -> bool:
        """Whether the model is loaded and ready."""
        return self._model is not None

    # -- lifecycle -----------------------------------------------------------------

    def load(self) -> None:
        """Load the model, naming the likely cause of any failure."""
        if self._model is not None:
            return

        factory = self._model_factory or self._import_model_class()
        started = time.monotonic()
        try:
            self._model = factory(
                self._model_name,
                device=self._resolved_device(),
                compute_type=self._precision,
            )
        except Exception as exc:  # noqa: BLE001 - the cause matters more than the type
            raise AsrLoadError(self._load_failure_message(exc)) from exc

        logger.info(
            "Loaded %s on %s in %.1f s",
            self.model_id,
            self._resolved_device(),
            time.monotonic() - started,
        )

    def unload(self) -> None:
        """Release the model and its device memory.

        Explicit because a local LLM may share the GPU; waiting for garbage collection means the
        LLM's allocation fails for no visible reason.
        """
        self._model = None

    # -- transcription -------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> AsrResult:
        """Transcribe one buffer, returning word tokens with **relative** timestamps."""
        if self._model is None:
            raise AsrLoadError("Transcribe called before the model was loaded")

        array = np.ascontiguousarray(audio, dtype=np.float32).ravel()
        if array.size == 0:
            return AsrResult(words=[], model_id=self.model_id)

        started = time.monotonic()
        segments, info = self._model.transcribe(
            array,
            language=self._language,
            beam_size=self._beam_size,
            word_timestamps=True,
            initial_prompt=prompt or None,
            condition_on_previous_text=False,
        )
        words = self._collect_words(segments)

        return AsrResult(
            words=words,
            language=getattr(info, "language", self._language),
            inference_seconds=time.monotonic() - started,
            model_id=self.model_id,
        )

    def warm_up(self) -> None:
        """Run one inference on silence, so the first real pass is not several times slower."""
        if self._model is None:
            return
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))

    # -- internals -----------------------------------------------------------------

    @staticmethod
    def _collect_words(segments: Any) -> list[WordToken]:
        """Flatten faster-whisper's nested segment/word output into word tokens.

        ``segments`` is a generator, so this is also where inference actually happens — the
        ``transcribe`` call above returns before doing any work.
        """
        words: list[WordToken] = []
        for segment in segments:
            for word in getattr(segment, "words", None) or []:
                text = str(getattr(word, "word", "")).strip()
                if not text:
                    continue
                probability = getattr(word, "probability", None)
                words.append(
                    WordToken(
                        text=text,
                        start=float(getattr(word, "start", 0.0)),
                        end=float(getattr(word, "end", 0.0)),
                        confidence=float(probability) if probability is not None else None,
                    )
                )
        return words

    def _resolved_device(self) -> str:
        """Turn ``auto`` into a concrete device."""
        if self._device != "auto":
            return self._device
        return "cuda" if _cuda_available() else "cpu"

    @staticmethod
    def _import_model_class() -> Any:
        """Import ``WhisperModel``, or explain exactly what to install."""
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise AsrLoadError(
                "faster-whisper is not installed. Install it with: "
                "uv sync --extra asr-whisper — or set asr.backend to 'mock' to run the pipeline "
                "without a real model."
            ) from exc
        return WhisperModel

    def _load_failure_message(self, exc: Exception) -> str:
        """Name the likely cause rather than reporting a bare failure (BE §15)."""
        detail = str(exc).lower()
        device = self._resolved_device()

        if "out of memory" in detail or "cuda" in detail and "memory" in detail:
            return (
                f"Not enough GPU memory to load {self._model_name} at {self._precision}. "
                "Try a smaller model, or set asr.device to 'cpu'."
            )
        if "cuda" in detail or "cudnn" in detail or "cublas" in detail:
            return (
                f"The CUDA runtime rejected loading {self._model_name}. "
                "Check the NVIDIA driver and cuDNN installation, or set asr.device to 'cpu'."
            )
        if "no such file" in detail or "not found" in detail or "404" in detail:
            return (
                f"Model {self._model_name!r} could not be found or downloaded. "
                "Check the model name and network access to the model repository."
            )
        return (
            f"Could not load {self._model_name} on {device} at {self._precision} "
            f"({type(exc).__name__}: {exc})."
        )


def _cuda_available() -> bool:
    """Whether a CUDA device appears usable.

    Checked through CTranslate2 rather than by importing torch, which faster-whisper does not
    require and which would be a heavy import for one boolean.
    """
    try:
        import ctranslate2
    except ImportError:
        return False
    try:
        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # noqa: BLE001 - a driver mismatch raises varied errors
        return False


def is_available() -> bool:
    """Whether the optional dependency is installed, without importing it."""
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None
