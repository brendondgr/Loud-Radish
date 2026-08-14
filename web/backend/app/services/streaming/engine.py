"""The streaming engine — the core of the system (BE §7).

Everything else is orchestration. This is the part that is genuinely hard, and the part where a
subtle mistake produces a transcript that looks plausible and is wrong.

One iteration, in order:

1. **Guards decide** whether this iteration should transcribe at all (§7.6). Buffer too short, no
   speech, buffer past the model's window, or too long since the last commit each take a different
   path, and each prevents a specific real failure.
2. **Inference runs** on the whole unconfirmed buffer, biased by the session prompt and the tail of
   committed text.
3. **Timestamps rebase** from buffer-relative to session-absolute (§7.5). Nothing downstream ever
   sees a relative time.
4. **The repetition filter** truncates degenerate looping before it can be committed.
5. **LocalAgreement commits** what this pass and the previous one agree on (§7.3); the rest stays
   as the hypothesis tail.
6. **The buffer trims** to the end of what was committed, preferring a sentence boundary, keeping a
   short acoustic tail so the first word after the cut is still recognised well (§7.4).
7. **Segments emit** for whatever the committed words completed (§7.7).

Time is measured in **audio consumed**, not wall-clock. That makes the commit timeout and the step
interval behave identically whether audio arrives from a live microphone or from a file replayed at
twenty times speed, and it makes every test deterministic.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ...config.schema import StreamingConfig
from ..asr.contract import AsrCapabilities, AsrResult, WordToken
from ..asr.prompting import PromptBuilder
from .agreement import LocalAgreement
from .buffer import StreamBuffer
from .events import CommittedSegment, EngineEvent, EngineNotice, HypothesisUpdate
from .guards import Severity, StreamGuards
from .segmenter import Segmenter, ends_sentence

logger = logging.getLogger(__name__)

#: Runs one inference pass. Supplied by the session manager, which owns the ASR lifecycle.
TranscribeFn = Callable[[np.ndarray, str | None], AsrResult]

#: How much committed text is fed back as rolling context, in characters.
ROLLING_CONTEXT_CHARS = 240


@dataclass
class EngineMetrics:
    """The numbers that say whether the engine is healthy (BE §16.3)."""

    inference_passes: int = 0
    #: Wall-clock seconds spent inside the model.
    inference_seconds: float = 0.0
    #: Audio seconds the engine has consumed.
    audio_seconds: float = 0.0
    #: Passes skipped by the silence gate — free speedup proportional to how much silence there is.
    skipped_silence: int = 0
    skipped_short_buffer: int = 0
    #: Commits produced by a guard rather than by agreement. A rising share means the buffer bounds
    #: or the timeout need tuning.
    forced_commits: int = 0
    natural_commits: int = 0
    repetition_truncations: int = 0
    #: Iterations where the hypothesis changed before committing. High values mean the buffer is
    #: too short for the model to settle.
    hypothesis_revisions: int = 0
    committed_words: int = 0
    #: Seconds between a word being spoken and it being committed.
    commit_latencies: list[float] = field(default_factory=list)

    @property
    def real_time_factor(self) -> float:
        """Audio seconds processed per wall-clock second inside the model.

        **This is the single most important number in the system.** Below 1.0 the queue grows
        without bound and the transcriber will never catch up on its own.
        """
        if self.inference_seconds <= 0.0:
            return 0.0
        return self.audio_seconds / self.inference_seconds

    @property
    def median_commit_latency(self) -> float:
        """Median seconds from spoken word to committed text."""
        return _percentile(self.commit_latencies, 50)

    @property
    def p95_commit_latency(self) -> float:
        """95th-percentile commit latency. Rising steadily means the system is falling behind."""
        return _percentile(self.commit_latencies, 95)

    @property
    def forced_commit_rate(self) -> float:
        """Share of commits that a guard forced rather than agreement producing."""
        total = self.forced_commits + self.natural_commits
        return self.forced_commits / total if total else 0.0

    @property
    def correction_rate(self) -> float:
        """Share of inference passes in which the hypothesis changed."""
        return self.hypothesis_revisions / self.inference_passes if self.inference_passes else 0.0

    def as_dict(self) -> dict[str, float | int]:
        """JSON-safe payload for the ``status`` event."""
        return {
            "rtf": round(self.real_time_factor, 3),
            "inference_passes": self.inference_passes,
            "audio_seconds": round(self.audio_seconds, 2),
            "commit_latency_s": round(self.median_commit_latency, 2),
            "commit_latency_p95_s": round(self.p95_commit_latency, 2),
            "forced_commit_rate": round(self.forced_commit_rate, 3),
            "correction_rate": round(self.correction_rate, 3),
            "committed_words": self.committed_words,
            "skipped_silence": self.skipped_silence,
            "repetition_truncations": self.repetition_truncations,
        }


class StreamingEngine:
    """Drives an ASR backend into a stable, append-only transcript."""

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
        self._capabilities = capabilities or AsrCapabilities()
        self._model_id = model_id
        self._prompts = prompt_builder

        self._buffer = StreamBuffer(retained_context_s=config.retained_context_s)
        self._agreement = LocalAgreement(agreement_count=config.agreement_count)
        self._guards = StreamGuards(config)
        self._segmenter = Segmenter(max_segment_s=config.max_segment_s, first_id=first_segment_id)

        self._metrics = EngineMetrics()
        self._audio_since_step = 0.0
        self._pause_pending = False
        self._last_hypothesis = ""
        self._committed_text: list[str] = []

    # -- state ---------------------------------------------------------------------

    @property
    def metrics(self) -> EngineMetrics:
        """Live engine metrics."""
        return self._metrics

    @property
    def hypothesis_text(self) -> str:
        """The current tentative tail."""
        return self._agreement.hypothesis_text

    @property
    def session_seconds(self) -> float:
        """Total audio consumed this session."""
        return self._buffer.session_seconds

    @property
    def buffer_seconds(self) -> float:
        """Length of the unconfirmed buffer."""
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
        """Add one frame and run an iteration if the step interval has elapsed.

        Args:
            frame: canonical-format audio.
            speaking: the VAD's debounced verdict for this frame.
            pause: whether a pause event fired, marking a preferred segment boundary.
        """
        self._buffer.append(frame)
        seconds = frame.size / 16_000
        self._audio_since_step += seconds
        self._metrics.audio_seconds += seconds

        if pause:
            self._pause_pending = True

        if self._audio_since_step < self._config.step_s:
            return []

        self._audio_since_step = 0.0
        return self._iterate(speaking)

    def flush(self) -> list[EngineEvent]:
        """Force-commit everything pending and close the open segment.

        Called when the session stops or the model is swapped, so the last words spoken are not
        lost waiting for agreement or a full stop that will never arrive.
        """
        events: list[EngineEvent] = []
        committed = self._agreement.force_commit()
        if committed:
            self._metrics.forced_commits += 1
            events.extend(self._absorb(committed, force_boundary=False))

        final = self._segmenter.flush()
        if final is not None:
            events.append(CommittedSegment(final))

        events.append(HypothesisUpdate(text="", start=self._buffer.end_absolute))
        self._last_hypothesis = ""
        return events

    def reset(self, first_segment_id: int | None = None) -> None:
        """Return to a clean state for a new session."""
        self._buffer.reset()
        self._agreement.reset()
        self._guards.reset()
        self._segmenter.reset(first_id=first_segment_id)
        self._metrics = EngineMetrics()
        self._audio_since_step = 0.0
        self._pause_pending = False
        self._last_hypothesis = ""
        self._committed_text = []

    def update_config(self, config: StreamingConfig) -> None:
        """Adopt new streaming settings without disturbing the buffer or committed text."""
        self._config = config
        self._guards.update_config(config)
        self._segmenter.update_max_duration(config.max_segment_s)

    def set_model(self, model_id: str, capabilities: AsrCapabilities) -> None:
        """Record a model swap. The caller flushes first, so nothing pending is misattributed."""
        self._model_id = model_id
        self._capabilities = capabilities

    # -- one iteration -------------------------------------------------------------

    def _iterate(self, speaking: bool) -> list[EngineEvent]:
        """Run the guards, then inference, then the commit policy."""
        decision = self._guards.before_inference(
            buffer_seconds=self._buffer.duration,
            audio_time=self._buffer.session_seconds,
            speaking=speaking,
            has_hypothesis=bool(self._agreement.hypothesis),
            has_open_segment=self._segmenter.has_pending,
        )

        if decision.guard == "silence-gate":
            self._metrics.skipped_silence += 1
        elif decision.guard == "minimum-buffer":
            self._metrics.skipped_short_buffer += 1

        if decision.guard == "silence-gate":
            events = (
                self._forced_commit(decision.guard, decision.reason)
                if decision.should_commit
                else []
            )
            # Silence is not worth keeping. Left in the buffer it costs inference time once speech
            # resumes, and a long enough pause would trip the maximum-buffer guard for no reason.
            self._buffer.trim_to(self._buffer.end_absolute)
            return events

        if decision.should_commit:
            return self._forced_commit(decision.guard, decision.reason)
        if not decision.should_infer:
            return []

        return self._infer_and_commit()

    def _infer_and_commit(self) -> list[EngineEvent]:
        """Transcribe the buffer and apply the commit policy."""
        events: list[EngineEvent] = []
        audio = self._buffer.audio
        started = time.monotonic()
        result = self._transcribe(audio, self._build_prompt())
        elapsed = time.monotonic() - started

        # Timed here rather than taken from the backend's self-report: real-time factor is the
        # single most important health number, and a backend that reports nothing (or reports
        # optimistically) would make it silently meaningless.
        self._metrics.inference_passes += 1
        self._metrics.inference_seconds += max(elapsed, result.inference_seconds)

        words = self._buffer.rebase(result.words)

        truncate_at = self._guards.check_repetition(words)
        if truncate_at is not None:
            words = words[:truncate_at]
            self._metrics.repetition_truncations += 1
            events.append(
                EngineNotice(
                    code="repetition",
                    message=(
                        "The speech model started repeating itself and was truncated. "
                        "This usually clears on its own; a smaller model is less prone to it."
                    ),
                    severity=Severity.INFO,
                )
            )

        committed = self._agreement.insert(words)
        if committed:
            self._metrics.natural_commits += 1
            events.extend(self._absorb(committed, force_boundary=False))
            self._trim_after_commit(committed)

        events.extend(self._hypothesis_events())
        return events

    def _forced_commit(self, guard: str, reason: str) -> list[EngineEvent]:
        """Commit the pending hypothesis because a guard said to."""
        events: list[EngineEvent] = []
        committed = self._agreement.force_commit()

        if committed:
            self._metrics.forced_commits += 1
            events.extend(self._absorb(committed, force_boundary=guard == "silence-gate"))
            self._trim_after_commit(committed)
        elif guard == "silence-gate" and self._segmenter.has_pending:
            # The speaker stopped and there was no hypothesis left to commit, but words are still
            # sitting in an unfinished segment. Close it: a pause is a segment boundary, and
            # waiting for a full stop that will never arrive strands the last thing said.
            final = self._segmenter.flush()
            if final is not None:
                events.append(CommittedSegment(final))
        elif guard == "maximum-buffer":
            # Nothing to commit but the buffer is still oversized — the model produced no usable
            # output for it. Discard it rather than resubmitting audio that already failed.
            self._buffer.hard_trim(self._config.min_buffer_s)

        if guard == "maximum-buffer":
            self._buffer.hard_trim(self._config.max_buffer_s * 0.5)
            events.append(
                EngineNotice(code="buffer-overflow", message=reason, severity=Severity.WARNING)
            )
        elif guard == "commit-timeout":
            logger.info("Forced commit: %s", reason)

        events.extend(self._hypothesis_events())
        return events

    # -- shared steps --------------------------------------------------------------

    def _absorb(self, words: list[WordToken], force_boundary: bool) -> list[EngineEvent]:
        """Record committed words, update metrics, and emit any completed segments."""
        now = self._buffer.session_seconds
        self._metrics.committed_words += len(words)
        self._metrics.commit_latencies.extend(max(0.0, now - word.end) for word in words)
        self._guards.note_commit(now)

        self._committed_text.extend(word.text for word in words)
        del self._committed_text[:-60]

        boundary = force_boundary or self._pause_pending
        self._pause_pending = False

        segments = self._segmenter.add(words, model_id=self._model_id, pause_boundary=boundary)
        return [CommittedSegment(segment) for segment in segments]

    def _trim_after_commit(self, committed: list[WordToken]) -> None:
        """Delete the audio for committed words, preferring a sentence boundary.

        Cutting mid-clause leaves the retained buffer starting on a fragment, which the model then
        has to make sense of without context. Cutting after a full stop gives it a clean start.
        """
        cut_at = committed[-1].end
        for word in reversed(committed):
            if ends_sentence(word.text):
                cut_at = word.end
                break
        self._buffer.trim_to(cut_at)

    def _hypothesis_events(self) -> list[EngineEvent]:
        """Emit a hypothesis update only when the tail actually changed.

        The tail updates roughly once a second for two hours. Re-sending an identical string is
        pure noise on the socket and, worse, re-renders the frontend's most-read element.
        """
        text = self._agreement.hypothesis_text
        if text == self._last_hypothesis:
            return []

        if self._last_hypothesis and text:
            self._metrics.hypothesis_revisions += 1
        self._last_hypothesis = text

        hypothesis = self._agreement.hypothesis
        start = hypothesis[0].start if hypothesis else self._buffer.end_absolute
        return [HypothesisUpdate(text=text, start=start)]

    def _build_prompt(self) -> str | None:
        """Compose the biasing prompt for this pass."""
        if self._prompts is None or not self._capabilities.accepts_prompt:
            return None
        rolling = " ".join(self._committed_text)[-ROLLING_CONTEXT_CHARS:]
        return self._prompts.build(rolling)


def _percentile(values: list[float], percentile: int) -> float:
    """Return a percentile of ``values``, or zero when there are none."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((percentile / 100.0) * (len(ordered) - 1))))
    return ordered[index]
