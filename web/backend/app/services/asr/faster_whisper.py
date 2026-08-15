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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PassConfidence:
    """What the model thought of its own output on one pass.

    Both are ``None`` when the model reported nothing, which downstream must read as *no opinion*
    rather than as a clean bill of health. Filtering on a value that was never measured deletes real
    words.
    """

    no_speech_prob: float | None = None
    avg_logprob: float | None = None


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
        vad_filter: bool = True,
    ) -> None:
        self._model_name = model
        self._device = device
        self._precision = precision
        self._language = language
        self._beam_size = beam_size
        self._model_factory = model_factory
        #: Whisper's built-in Silero filter. The first line of defence against invented text:
        #: non-speech that is never decoded cannot be transcribed into something.
        self._vad_filter = vad_filter
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
            # Strips non-speech from the buffer before decoding. The cheapest fix for invented
            # text there is, because text that is never decoded cannot be invented — and the
            # filter is a small Silero model, which is far cheaper than the Whisper pass it saves.
            vad_filter=self._vad_filter,
        )
        words, confidence = self._collect(segments)

        return AsrResult(
            words=words,
            language=getattr(info, "language", self._language),
            inference_seconds=time.monotonic() - started,
            model_id=self.model_id,
            no_speech_prob=confidence.no_speech_prob,
            avg_logprob=confidence.avg_logprob,
        )

    def warm_up(self) -> None:
        """Run one inference on silence, so the first real pass is not several times slower."""
        if self._model is None:
            return
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))

    # -- internals -----------------------------------------------------------------

    @staticmethod
    def _collect(segments: Any) -> tuple[list[WordToken], PassConfidence]:
        """Flatten faster-whisper's nested output into word tokens, keeping its confidence.

        ``segments`` is a generator, so this is also where inference actually happens — the
        ``transcribe`` call above returns before doing any work.

        Each segment carries ``no_speech_prob`` and ``avg_logprob`` alongside its words. An earlier
        version read the words and dropped the rest, which is why hallucinated text was invisible to
        everything downstream: the model had already said it was probably not speech, and nobody was
        listening.
        """
        words: list[WordToken] = []
        no_speech: list[tuple[float, float]] = []
        logprobs: list[tuple[float, float]] = []

        for segment in segments:
            start = _as_float(getattr(segment, "start", None))
            end = _as_float(getattr(segment, "end", None))
            # Weight by how much audio the segment covers, so a half-second aside cannot outvote
            # twenty seconds of speech. A zero-length segment still counts, minimally.
            weight = max(end - start, 0.0) if start is not None and end is not None else 0.0

            probability = _as_float(getattr(segment, "no_speech_prob", None))
            if probability is not None:
                no_speech.append((probability, weight))
            logprob = _as_float(getattr(segment, "avg_logprob", None))
            if logprob is not None:
                logprobs.append((logprob, weight))

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

        return words, PassConfidence(
            no_speech_prob=_weighted_mean(no_speech),
            avg_logprob=_weighted_mean(logprobs),
        )

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
                "uv sync — or set asr.backend to 'mock' to run the pipeline "
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
            return self._gpu_rejection_message()
        if "no such file" in detail or "not found" in detail or "404" in detail:
            return (
                f"Model {self._model_name!r} could not be found or downloaded. "
                "Check the model name and network access to the model repository."
            )
        return (
            f"Could not load {self._model_name} on {device} at {self._precision} "
            f"({type(exc).__name__}: {exc})."
        )

    def _gpu_rejection_message(self) -> str:
        """Say which GPU stack failed, which is not always the one the exception names.

        CTranslate2 drives ROCm through the CUDA API, so an AMD machine whose ROCm build has been
        replaced — which is what any ``uv sync`` touching CTranslate2 does — raises an error saying
        *CUDA*. Repeating that back sends the owner of a Radeon to check an NVIDIA driver they will
        never have. The acceleration report knows what hardware is actually present, so it is asked.
        """
        try:
            from .acceleration import detect

            report = detect()
        except Exception:  # noqa: BLE001 - a diagnosis must never replace the failure it explains
            report = None

        if report is not None and report.hardware == "rocm":
            steps = "; ".join(report.remedy)
            return (
                f"The GPU runtime rejected loading {self._model_name}. {report.summary} "
                + (f"Fix it with: {steps} — or set asr.device to 'cpu'." if steps else "")
            ).strip()

        return (
            f"The CUDA runtime rejected loading {self._model_name}. "
            "Check the NVIDIA driver and cuDNN installation, or set asr.device to 'cpu'."
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


#: Every device and precision the UI could offer, before the machine is consulted.
ALL_DEVICES = ("auto", "cpu", "cuda")
ALL_PRECISIONS = ("int8", "float16", "float32")


def compute_support() -> dict[str, list[str]]:
    """Which devices and precisions *this machine* can actually run.

    Offering ``float16`` on a CPU-only build is offering a setting that fails at load time, and the
    failure arrives when the user presses record rather than when they choose it. CTranslate2 knows
    the answer, so it is asked rather than guessed.

    Returns a mapping of device to the precisions it supports, plus a ``devices`` key listing the
    devices themselves. Everything is offered unconditionally when CTranslate2 cannot be reached —
    a wrong guess that permits too much is recoverable, one that hides a working option is not.
    """
    try:
        import ctranslate2
    except ImportError:
        return {"devices": list(ALL_DEVICES), **{d: list(ALL_PRECISIONS) for d in ALL_DEVICES}}

    devices: list[str] = ["auto", "cpu"]
    support: dict[str, list[str]] = {}

    try:
        cuda_count = int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001 - the probe itself must never fail a settings page
        cuda_count = 0
    if cuda_count > 0:
        devices.append("cuda")

    for device in ("cpu", "cuda"):
        if device == "cuda" and cuda_count == 0:
            support[device] = []
            continue
        try:
            raw = set(ctranslate2.get_supported_compute_types(device))
        except Exception:  # noqa: BLE001 - unknown is better handled as permissive
            support[device] = list(ALL_PRECISIONS)
            continue
        # CTranslate2 reports composites such as ``int8_float32``; a precision counts as supported
        # when it appears in any of them, which is how it is actually selected.
        support[device] = [p for p in ALL_PRECISIONS if any(p in name for name in raw)]

    # "auto" resolves to CUDA when present, so it can offer whatever that device can.
    support["auto"] = support["cuda"] if cuda_count > 0 else support["cpu"]
    return {"devices": devices, **support}


def _as_float(value: Any) -> float | None:
    """Read a numeric attribute, or ``None`` when it is absent or unreadable.

    Absent is deliberately distinct from zero. ``no_speech_prob`` of 0.0 means the model is certain
    there *is* speech; a missing attribute means it did not say, and the two must not be conflated
    by a filter that will delete words on the strength of it.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN is not an opinion either


def _weighted_mean(samples: list[tuple[float, float]]) -> float | None:
    """Average ``(value, weight)`` pairs by weight, falling back to a plain mean.

    Weighted by segment duration so a half-second aside cannot outvote twenty seconds of speech.
    The fallback matters: a pass can legitimately produce segments with no duration between them,
    and returning nothing there would look like the model having no opinion when it had one.
    """
    if not samples:
        return None
    total_weight = sum(weight for _, weight in samples)
    if total_weight <= 0:
        return sum(value for value, _ in samples) / len(samples)
    return sum(value * weight for value, weight in samples) / total_weight
