"""Seam A — the ASR interface (BE §6.2).

This is one of the two interfaces the whole architecture rests on. Everything else is replaceable
later; this is not. It exists so the choice of speech model is a configuration value rather than an
architectural commitment, and so the streaming engine never learns which model it is driving
(constraint **C6**).

**The critical detail.** Timestamps returned by a backend are relative to the *submitted audio
array*, never to wall-clock or session time. Translating them into session-absolute time is the
streaming engine's job, and getting that boundary wrong is the most common source of bugs in systems
like this. A backend that tries to be helpful and return absolute times breaks the engine silently.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

RunsOn = Literal["cpu", "cuda", "remote"]


@dataclass(frozen=True)
class WordToken:
    """One recognised word, with times **relative to the submitted audio array**."""

    text: str
    #: Seconds from the start of the submitted array. Never wall-clock, never session-absolute.
    start: float
    #: Seconds from the start of the submitted array.
    end: float
    #: ``None`` when the backend cannot provide one. Never fabricated — a made-up confidence is
    #: worse than none, because the UI will highlight low-confidence words based on it.
    confidence: float | None = None

    @property
    def duration(self) -> float:
        """How long the word took to say."""
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class AsrResult:
    """What one inference pass produced."""

    words: list[WordToken]
    #: The language the backend detected or was told to use.
    language: str | None = None
    #: Wall-clock seconds the inference itself took. Feeds the real-time factor metric.
    inference_seconds: float = 0.0
    #: Which backend and model produced this, for the segment's ``model_id`` field.
    model_id: str = ""

    @property
    def text(self) -> str:
        """The recognised text as one string."""
        return " ".join(word.text for word in self.words).strip()

    @property
    def audio_end(self) -> float:
        """The relative end time of the last word, or zero when nothing was recognised."""
        return self.words[-1].end if self.words else 0.0

    def is_empty(self) -> bool:
        """Whether this pass recognised nothing."""
        return not self.words


EMPTY_RESULT = AsrResult(words=[])


@dataclass(frozen=True)
class AsrCapabilities:
    """What a backend can do (BE §6.3).

    Backends differ, and the alternative to declaring capabilities is special-casing model names
    throughout the engine. Each flag exists because some part of the pipeline behaves differently
    without it.
    """

    #: Word-level timestamps. Without them, buffer trimming falls back to duration-estimated cuts.
    word_timestamps: bool = True
    #: The model consumes a continuous stream and emits stable text. Bypasses the commit machinery.
    streaming_native: bool = False
    #: The backend accepts a biasing prompt, so term biasing is available.
    accepts_prompt: bool = True
    #: Language codes supported, or ``["*"]`` for "anything".
    languages: list[str] = field(default_factory=lambda: ["*"])
    #: Hard ceiling on submitted audio. Whisper-family models have a 30 s window.
    max_audio_seconds: float | None = 30.0
    #: Where inference runs. Affects device selection and warm-up cost.
    runs_on: RunsOn = "cpu"
    #: Whether the backend reports per-word confidence.
    confidence: bool = False

    def supports_language(self, language: str | None) -> bool:
        """Whether ``language`` can be requested of this backend."""
        if language is None or "*" in self.languages:
            return True
        return language in self.languages

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``GET /api/asr/models``."""
        return {
            "word_timestamps": self.word_timestamps,
            "streaming_native": self.streaming_native,
            "accepts_prompt": self.accepts_prompt,
            "languages": list(self.languages),
            "max_audio_seconds": self.max_audio_seconds,
            "runs_on": self.runs_on,
            "confidence": self.confidence,
        }


class AsrError(RuntimeError):
    """Base class for backend failures the pipeline may be able to recover from."""


class AsrUnavailableError(AsrError):
    """The backend cannot be used — a missing dependency, model file, or device."""


class AsrLoadError(AsrError):
    """The model failed to load. The message must name the likely cause (BE §15)."""


class AsrBackend(ABC):
    """A speech-to-text backend.

    Implementations are constructed cheaply and do their expensive work in :meth:`load`, so the
    session manager can enumerate available backends without paying to instantiate every model.
    """

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Stable identifier used in configuration, e.g. ``"faster-whisper"``."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Identifier of the loaded model, recorded on every segment it produces.

        Segments carry this because a model swap mid-session changes what produced the text, and a
        transcript whose provenance is ambiguous is harder to trust than one that is explicit.
        """

    @property
    @abstractmethod
    def capabilities(self) -> AsrCapabilities:
        """What this backend can do. Read after :meth:`load`."""

    @property
    @abstractmethod
    def is_loaded(self) -> bool:
        """Whether the model is loaded and ready to transcribe."""

    @abstractmethod
    def load(self) -> None:
        """Load the model. May take 5–30 seconds and significant memory.

        Raises:
            AsrLoadError: with a message naming the likely cause — insufficient VRAM, a missing
                file, the wrong device — because "failed to load" alone leaves the user with
                nothing to act on.
        """

    @abstractmethod
    def unload(self) -> None:
        """Release the model and its device memory.

        Explicit rather than left to garbage collection: this matters when a local LLM shares the
        same GPU (BE §10.5).
        """

    @abstractmethod
    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> AsrResult:
        """Transcribe a contiguous array of canonical-format audio.

        Args:
            audio: 16 kHz mono float32, normalised to −1.0…+1.0.
            prompt: optional biasing text (BE §6.5). Ignored when
                :attr:`AsrCapabilities.accepts_prompt` is false.

        Returns:
            Word tokens with times **relative to** ``audio``.
        """

    def warm_up(self) -> None:
        """Run one inference on a short buffer of silence.

        The first real inference is otherwise several times slower than steady state, which corrupts
        latency measurements and makes the first seconds of a talk feel broken. Backends with no
        warm-up cost may leave this as the default.
        """
        if self.is_loaded:
            self.transcribe(np.zeros(16_000, dtype=np.float32))

    def describe(self) -> dict[str, Any]:
        """A JSON-safe description for the model list."""
        return {
            "backend": self.backend_id,
            "model": self.model_id,
            "loaded": self.is_loaded,
            "capabilities": self.capabilities.as_dict(),
        }
