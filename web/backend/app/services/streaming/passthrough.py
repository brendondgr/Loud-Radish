"""The bypass path for streaming-native backends (BE §7.8).

Transducer models — Parakeet, Moonshine, Nemotron-style — read audio in a single pass and emit text
as they go, without a per-token loop. They are substantially faster and architecturally far less
prone to hallucinating during silence, and crucially their output is already stable: there is no
revision to absorb, so the entire LocalAgreement machinery is unnecessary.

**The output contract is identical.** Same segment structure, same committed/hypothesis event
stream, same metrics. Nothing downstream can tell which engine produced an event, which is the whole
point of the capability flag — the choice of model must not leak past this layer.
"""

from __future__ import annotations

import logging
import time

import numpy as np

from ...config.schema import StreamingConfig
from ..asr.contract import AsrCapabilities, WordToken
from ..asr.prompting import PromptBuilder
from .agreement import normalise, strip_repeated_prefix
from .buffer import StreamBuffer
from .engine import EngineMetrics, TranscribeFn
from .events import CommittedSegment, EngineEvent, HypothesisUpdate
from .segmenter import Segmenter

logger = logging.getLogger(__name__)

#: How many recently emitted words are remembered for boundary de-duplication.
EMITTED_TAIL_WORDS = 12


class PassthroughEngine:
    """Drives a streaming-native backend, committing its output directly.

    Interchangeable with :class:`~.engine.StreamingEngine`: same constructor shape, same methods,
    same events.
    """

    def __init__(
        self,
        config: StreamingConfig,
        transcribe: TranscribeFn,
        capabilities: AsrCapabilities | None = None,
        model_id: str = "",
        prompt_builder: PromptBuilder | None = None,
        first_segment_id: int = 1,
    ) -> None:
        self._config = config
        self._transcribe = transcribe
        self._capabilities = capabilities or AsrCapabilities(streaming_native=True)
        self._model_id = model_id
        self._prompts = prompt_builder

        # No retained context: a streaming-native model carries its own state across calls, so
        # re-submitting audio it has already consumed would duplicate text.
        self._buffer = StreamBuffer(retained_context_s=0.0)
        self._segmenter = Segmenter(max_segment_s=config.max_segment_s, first_id=first_segment_id)
        self._metrics = EngineMetrics()
        self._audio_since_step = 0.0
        self._pause_pending = False
        self._last_hypothesis = ""
        self._emitted_tail: list[str] = []

    # -- state ---------------------------------------------------------------------

    @property
    def metrics(self) -> EngineMetrics:
        """Live engine metrics, in the same shape the LocalAgreement path reports."""
        return self._metrics

    @property
    def hypothesis_text(self) -> str:
        """Always empty: a streaming-native model's output is committed as it arrives."""
        return ""

    @property
    def session_seconds(self) -> float:
        """Total audio consumed this session."""
        return self._buffer.session_seconds

    @property
    def buffer_seconds(self) -> float:
        """Length of the audio not yet submitted."""
        return self._buffer.duration

    @property
    def next_segment_id(self) -> int:
        """The id the next completed segment will receive."""
        return self._segmenter.next_id

    # -- driving -------------------------------------------------------------------

    def add_audio(
        self,
        frame: np.ndarray,
        speaking: bool = True,
        pause: bool = False,
    ) -> list[EngineEvent]:
        """Add one frame and submit accumulated audio on the step interval."""
        self._buffer.append(frame)
        seconds = frame.size / 16_000
        self._audio_since_step += seconds
        self._metrics.audio_seconds += seconds

        if pause:
            self._pause_pending = True

        if self._audio_since_step < self._config.step_s:
            return []
        self._audio_since_step = 0.0

        if not speaking and not self._pause_pending:
            # Still cheap to skip silence, even for a model that tolerates it.
            self._metrics.skipped_silence += 1
            self._buffer.clear()
            return []

        return self._submit()

    def flush(self) -> list[EngineEvent]:
        """Submit anything buffered and close the open segment."""
        events = self._submit() if self._buffer.duration > 0 else []

        final = self._segmenter.flush()
        if final is not None:
            events.append(CommittedSegment(final))

        if self._last_hypothesis:
            events.append(HypothesisUpdate(text="", start=self._buffer.end_absolute))
            self._last_hypothesis = ""
        return events

    def reset(self, first_segment_id: int | None = None) -> None:
        """Return to a clean state for a new session."""
        self._buffer.reset()
        self._segmenter.reset(first_id=first_segment_id)
        self._metrics = EngineMetrics()
        self._audio_since_step = 0.0
        self._pause_pending = False
        self._last_hypothesis = ""
        self._emitted_tail = []

    def update_config(self, config: StreamingConfig) -> None:
        """Adopt new streaming settings."""
        self._config = config
        self._segmenter.update_max_duration(config.max_segment_s)

    def set_model(self, model_id: str, capabilities: AsrCapabilities) -> None:
        """Record a model swap."""
        self._model_id = model_id
        self._capabilities = capabilities

    # -- internals -----------------------------------------------------------------

    def _submit(self) -> list[EngineEvent]:
        """Transcribe the accumulated audio and commit all of it."""
        audio = self._buffer.audio
        if audio.size == 0:
            return []

        prompt = (
            self._prompts.build() if self._prompts and self._capabilities.accepts_prompt else None
        )
        started = time.monotonic()
        result = self._transcribe(audio, prompt)
        elapsed = time.monotonic() - started

        self._metrics.inference_passes += 1
        self._metrics.inference_seconds += max(elapsed, result.inference_seconds)

        words: list[WordToken] = self._buffer.rebase(result.words)

        # Everything submitted has been consumed, whether or not it produced words.
        self._buffer.clear()

        # A word spanning a chunk boundary can arrive from two consecutive calls. The offline path
        # strips exactly this, and the two engines must not differ on whether the transcript gains
        # a duplicate at every boundary.
        words = strip_repeated_prefix(words, self._emitted_tail)
        if not words:
            return []
        self._emitted_tail = (self._emitted_tail + [normalise(w.text) for w in words])[
            -EMITTED_TAIL_WORDS:
        ]

        now = self._buffer.session_seconds
        self._metrics.natural_commits += 1
        self._metrics.committed_words += len(words)
        self._metrics.commit_latencies.extend(max(0.0, now - word.end) for word in words)

        boundary = self._pause_pending
        self._pause_pending = False

        segments = self._segmenter.add(words, model_id=self._model_id, pause_boundary=boundary)
        return [CommittedSegment(segment) for segment in segments]


def build_engine(
    config: StreamingConfig,
    transcribe: TranscribeFn,
    capabilities: AsrCapabilities,
    model_id: str = "",
    prompt_builder: PromptBuilder | None = None,
    first_segment_id: int = 1,
):  # noqa: ANN201 - the union of both engines; they are structurally identical
    """Choose the engine the loaded backend needs.

    The decision is made from the declared capability, never from the model's name. That is what
    keeps adding a backend from meaning editing the engine.
    """
    from .engine import StreamingEngine

    engine_class = PassthroughEngine if capabilities.streaming_native else StreamingEngine
    logger.info("Using %s for %s", engine_class.__name__, model_id or "the configured backend")
    return engine_class(
        config=config,
        transcribe=transcribe,
        capabilities=capabilities,
        model_id=model_id,
        prompt_builder=prompt_builder,
        first_segment_id=first_segment_id,
    )
