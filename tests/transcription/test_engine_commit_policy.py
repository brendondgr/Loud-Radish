"""The streaming engine end to end, driven by the scripted mock backend (BE §7, §19.3).

Everything here is deterministic: the engine measures time in audio consumed rather than wall-clock,
and the mock replays an exact word sequence. That is what makes the pathological cases — continuous
speech, long silence, a repetition loop — assertions rather than observations.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.config.schema import AsrConfig, StreamingConfig
from app.services.asr import (
    AsrCapabilities,
    MockAsrBackend,
    MockScript,
    PromptBuilder,
    positional_audio,
)
from app.services.audio.formats import SAMPLE_RATE
from app.services.streaming import (
    CommittedSegment,
    EngineNotice,
    HypothesisUpdate,
    StreamingEngine,
    build_engine,
)

FRAME_SECONDS = 0.25
SILENT_FRAME = np.zeros(int(FRAME_SECONDS * SAMPLE_RATE), dtype=np.float32)

#: A talk long enough that the buffer trims many times over the runs below.
TALK = (
    "the central claim is that these operators commute only on a dense subspace. "
    "outside it the bracket simply is not defined. "
    "so let me set the counterexample up properly now. "
    "take the position operator on the half line and the dilation generator alongside. "
    "both are symmetric on smooth compactly supported functions. "
    "neither is self adjoint there and the deficiency indices differ."
)


def config(**overrides: object) -> StreamingConfig:
    settings: dict = {
        "agreement_count": 2,
        "step_s": 0.5,
        "min_buffer_s": 0.4,
        "max_buffer_s": 25.0,
        "commit_timeout_s": 15.0,
        "retained_context_s": 0.5,
        "max_segment_s": 30.0,
        **overrides,
    }
    return StreamingConfig(**settings)


def make_engine(
    text: str = TALK,
    streaming: StreamingConfig | None = None,
    **script_kwargs: object,
) -> tuple[StreamingEngine, MockAsrBackend]:
    backend = MockAsrBackend(MockScript(words=text.split(), **script_kwargs))  # type: ignore[arg-type]
    backend.load()
    engine = StreamingEngine(
        config=streaming or config(),
        transcribe=backend.transcribe,
        capabilities=backend.capabilities,
        model_id=backend.model_id,
    )
    return engine, backend


def drive(
    engine,
    frames: int,
    speaking: bool = True,
    frame: np.ndarray | None = None,
    pause: bool = False,
) -> list:
    """Feed frames and collect every event the engine emitted.

    Frames carry their own absolute session time unless an explicit ``frame`` is given, so the mock
    returns the words actually present in whatever slice of audio the engine submitted.
    """
    events: list = []
    for _ in range(frames):
        chunk = (
            frame if frame is not None else positional_audio(engine.session_seconds, FRAME_SECONDS)
        )
        events.extend(engine.add_audio(chunk, speaking=speaking, pause=pause))
    return events


def segments(events: list) -> list:
    return [event.segment for event in events if isinstance(event, CommittedSegment)]


def hypotheses(events: list) -> list[str]:
    return [event.text for event in events if isinstance(event, HypothesisUpdate)]


class TestNormalOperation:
    def test_audio_shorter_than_the_step_runs_no_inference(self) -> None:
        engine, backend = make_engine()
        engine.add_audio(positional_audio(0.0, FRAME_SECONDS))
        assert backend.pass_count == 0

    def test_inference_runs_once_the_step_interval_elapses(self) -> None:
        engine, backend = make_engine()
        drive(engine, frames=4)
        assert backend.pass_count >= 1

    def test_committed_text_accumulates_into_segments(self) -> None:
        engine, _ = make_engine()
        events = drive(engine, frames=40)
        assert segments(events)

    def test_committed_text_is_never_revised(self) -> None:
        """Constraint C4. A segment that changes after emission breaks the reader's position."""
        engine, _ = make_engine()
        events = drive(engine, frames=60)

        emitted = segments(events)
        by_id = {segment.id: segment.text for segment in emitted}
        assert len(by_id) == len(emitted), "a segment id was emitted twice"

    def test_no_word_is_committed_twice(self) -> None:
        """The trim retains acoustic context, so the model re-emits words it already produced."""
        engine, _ = make_engine("alpha beta gamma delta epsilon zeta eta theta iota kappa")
        events = drive(engine, frames=80)

        text = " ".join(segment.text for segment in segments(events))
        words = text.split()
        assert len(words) == len(set(words)), f"duplicated words in: {text}"

    def test_segment_ids_are_contiguous_and_increasing(self) -> None:
        engine, _ = make_engine("alpha one. beta two. gamma three. delta four. epsilon five.")
        ids = [segment.id for segment in segments(drive(engine, frames=80))]
        assert ids == sorted(ids)
        assert ids == list(range(1, len(ids) + 1))

    def test_timestamps_are_session_absolute_and_increase(self) -> None:
        """Relative times must never escape the engine."""
        engine, _ = make_engine("alpha one. beta two. gamma three. delta four. epsilon five.")
        emitted = segments(drive(engine, frames=120))

        assert emitted
        for earlier, later in zip(emitted, emitted[1:], strict=False):
            assert earlier.end <= later.start + 1e-6
        assert emitted[-1].end > 1.0

    def test_the_hypothesis_is_emitted_and_changes(self) -> None:
        engine, _ = make_engine()
        assert any(hypotheses(drive(engine, frames=30)))

    def test_an_unchanged_hypothesis_is_not_re_sent(self) -> None:
        """It updates once a second for two hours; re-sending an identical string is pure noise."""
        engine, _ = make_engine("static text here", revise_last=0)
        events = drive(engine, frames=60)

        emitted = hypotheses(events)
        assert len(emitted) == len(set(emitted)) or len(emitted) < 10


class TestBufferBehaviour:
    def test_the_buffer_stays_bounded_over_a_long_run(self) -> None:
        """Constraints C1 and C2, in miniature."""
        engine, _ = make_engine("alpha beta gamma delta epsilon zeta. " * 12)
        drive(engine, frames=400)
        assert engine.buffer_seconds < 25.0

    def test_submitted_audio_never_exceeds_the_model_window(self) -> None:
        engine, backend = make_engine(TALK * 4, streaming=config(max_buffer_s=10.0))
        drive(engine, frames=300)
        assert max(backend.audio_seconds_seen) <= 12.0

    def test_session_seconds_tracks_all_audio_consumed(self) -> None:
        engine, _ = make_engine()
        drive(engine, frames=40)
        assert engine.session_seconds == pytest.approx(40 * FRAME_SECONDS)


class TestSilence:
    def test_silence_skips_inference(self) -> None:
        """The hallucination gate. This is the single most effective mitigation."""
        engine, backend = make_engine()
        drive(engine, frames=40, speaking=False, frame=SILENT_FRAME)
        assert backend.pass_count == 0
        assert engine.metrics.skipped_silence > 0

    def test_a_long_silence_produces_no_text(self) -> None:
        engine, _ = make_engine("phantom text from silence", hallucinate_on_silence=True)
        events = drive(engine, frames=120, speaking=False, frame=SILENT_FRAME)
        assert segments(events) == []

    def test_speech_then_silence_commits_the_pending_tail(self) -> None:
        """Waiting for agreement on audio that is not coming would strand the last words."""
        engine, _ = make_engine("the final words before the pause")
        drive(engine, frames=20)
        events = drive(engine, frames=8, speaking=False, frame=SILENT_FRAME)
        assert segments(events)

    def test_speech_resuming_after_a_pause_is_transcribed(self) -> None:
        """BE §19.3: a speaker starting mid-word after a pause."""
        engine, _ = make_engine()
        drive(engine, frames=12)
        drive(engine, frames=12, speaking=False, frame=SILENT_FRAME)
        assert segments(drive(engine, frames=40))

    def test_a_long_pause_does_not_fill_the_buffer_with_silence(self) -> None:
        """Otherwise a two-minute pause trips the maximum-buffer guard for no reason."""
        engine, _ = make_engine()
        drive(engine, frames=12)
        drive(engine, frames=200, speaking=False, frame=SILENT_FRAME)
        assert engine.buffer_seconds < 2.0


class TestGuardsInTheEngine:
    def test_continuous_speech_eventually_commits(self) -> None:
        """BE §19.3 and §5.3: two minutes of unbroken speech must not look frozen."""
        # A tail that is rewritten on every pass can never reach agreement, and there is no
        # punctuation and no pause anywhere — so only the commit timeout can break this.
        engine, _ = make_engine(
            " ".join(f"word{i}" for i in range(300)),
            streaming=config(commit_timeout_s=4.0, max_buffer_s=25.0),
            unstable_tail=40,
        )
        events = drive(engine, frames=200)

        assert segments(events), "continuous speech produced no committed text"
        assert engine.metrics.forced_commits > 0

    def test_an_oversized_buffer_forces_a_commit_and_hard_trims(self) -> None:
        # A model producing nothing usable: without the guard the buffer would grow past the
        # model's window and every later pass would be silently truncated.
        engine, _ = make_engine("", streaming=config(max_buffer_s=6.0, step_s=0.5))
        events = drive(engine, frames=120)

        assert engine.buffer_seconds <= 7.0
        assert any(
            isinstance(event, EngineNotice) and event.code == "buffer-overflow" for event in events
        )

    def test_a_repetition_loop_is_truncated_and_reported(self) -> None:
        """Whisper's degenerate looping. Left alone it fills the transcript forever."""
        engine, _ = make_engine("fine", repeat_ngram=["and", "so"], repeat_after_pass=1)
        events = drive(engine, frames=60)

        assert engine.metrics.repetition_truncations > 0
        notices = [e for e in events if isinstance(e, EngineNotice) and e.code == "repetition"]
        assert notices
        assert "smaller model" in notices[0].message

    def test_a_repetition_loop_does_not_fill_the_transcript(self) -> None:
        engine, _ = make_engine("fine", repeat_ngram=["and", "so"], repeat_after_pass=1)
        events = drive(engine, frames=120)

        text = " ".join(segment.text for segment in segments(events))
        assert text.count("and so") <= 6, f"loop leaked into the transcript: {text}"


class TestFlush:
    def test_flush_commits_the_pending_hypothesis(self) -> None:
        engine, _ = make_engine("words with no terminating punctuation")
        drive(engine, frames=20)
        events = engine.flush()
        assert segments(events)

    def test_flush_clears_the_hypothesis_display(self) -> None:
        """An empty hypothesis event is meaningful: it tells the frontend to clear the tail."""
        engine, _ = make_engine()
        drive(engine, frames=20)
        events = engine.flush()
        assert hypotheses(events)[-1] == ""

    def test_flush_on_an_untouched_engine_is_harmless(self) -> None:
        engine, _ = make_engine()
        assert segments(engine.flush()) == []


class TestMetrics:
    def test_real_time_factor_is_reported(self) -> None:
        engine, _ = make_engine("some words here", inference_seconds=0.001)
        drive(engine, frames=40)
        assert engine.metrics.real_time_factor > 0

    def test_a_zero_inference_time_reports_zero_rather_than_dividing(self) -> None:
        engine, _ = make_engine()
        assert engine.metrics.real_time_factor == 0.0

    def test_commit_latency_is_measured(self) -> None:
        engine, _ = make_engine()
        drive(engine, frames=60)
        assert engine.metrics.median_commit_latency >= 0.0
        assert engine.metrics.p95_commit_latency >= engine.metrics.median_commit_latency

    def test_forced_commit_rate_is_a_fraction(self) -> None:
        engine, _ = make_engine(
            " ".join(f"word{i}" for i in range(300)), streaming=config(commit_timeout_s=3.0)
        )
        drive(engine, frames=120)
        assert 0.0 <= engine.metrics.forced_commit_rate <= 1.0

    def test_metrics_serialise_for_the_status_event(self) -> None:
        engine, _ = make_engine()
        drive(engine, frames=40)
        payload = engine.metrics.as_dict()
        assert "rtf" in payload
        assert isinstance(payload["committed_words"], int)


class TestLifecycle:
    def test_reset_returns_the_engine_to_a_clean_state(self) -> None:
        engine, _ = make_engine()
        drive(engine, frames=60)
        engine.reset(first_segment_id=1)

        assert engine.session_seconds == 0.0
        assert engine.buffer_seconds == 0.0
        assert engine.hypothesis_text == ""
        assert engine.next_segment_id == 1

    def test_config_updates_apply_without_disturbing_the_transcript(self) -> None:
        engine, _ = make_engine()
        drive(engine, frames=40)
        before = engine.next_segment_id

        engine.update_config(config(step_s=1.0, max_segment_s=10.0))
        assert engine.next_segment_id == before

    def test_a_model_swap_records_the_new_model_on_later_segments(self) -> None:
        """BE §19.3: provenance must not be ambiguous after a mid-session swap."""
        engine, _ = make_engine("first model text. and then more text here.")
        drive(engine, frames=40)
        engine.flush()

        engine.set_model("faster-whisper:small:int8", AsrCapabilities())
        later = segments(drive(engine, frames=60))
        assert all(segment.model_id == "faster-whisper:small:int8" for segment in later)


class TestPromptWiring:
    def test_the_session_prompt_reaches_the_backend(self) -> None:
        backend = MockAsrBackend(MockScript(words="alpha beta gamma delta epsilon zeta".split()))
        backend.load()
        engine = StreamingEngine(
            config=config(),
            transcribe=backend.transcribe,
            capabilities=backend.capabilities,
            prompt_builder=PromptBuilder(AsrConfig(session_prompt="self-adjoint extensions")),
        )
        drive(engine, frames=20)
        assert any("self-adjoint" in (p or "") for p in backend.prompts_seen)

    def test_no_prompt_is_sent_when_the_backend_cannot_use_one(self) -> None:
        backend = MockAsrBackend(
            MockScript(words="a b c".split()),
            capabilities=AsrCapabilities(accepts_prompt=False),
        )
        backend.load()
        engine = StreamingEngine(
            config=config(),
            transcribe=backend.transcribe,
            capabilities=backend.capabilities,
            prompt_builder=PromptBuilder(AsrConfig(session_prompt="ignored")),
        )
        drive(engine, frames=20)
        assert all(prompt is None for prompt in backend.prompts_seen)

    def test_committed_text_feeds_back_as_rolling_context(self) -> None:
        """Improves continuity across buffer boundaries and keeps a term spelled consistently."""
        backend = MockAsrBackend(
            MockScript(words="deficiency indices differ here markedly".split())
        )
        backend.load()
        engine = StreamingEngine(
            config=config(),
            transcribe=backend.transcribe,
            capabilities=backend.capabilities,
            prompt_builder=PromptBuilder(AsrConfig(session_prompt="", use_rolling_prompt=True)),
        )
        drive(engine, frames=60)
        assert any("deficiency" in (prompt or "") for prompt in backend.prompts_seen)


class TestEngineSelection:
    def test_an_offline_backend_gets_the_full_commit_machinery(self) -> None:
        backend = MockAsrBackend(MockScript(words=["a"]))
        engine = build_engine(
            config=config(),
            transcribe=backend.transcribe,
            capabilities=AsrCapabilities(streaming_native=False),
        )
        assert isinstance(engine, StreamingEngine)

    def test_the_decision_comes_from_the_capability_not_the_model_name(self) -> None:
        from app.services.streaming import PassthroughEngine

        backend = MockAsrBackend(MockScript(words=["a"]))
        engine = build_engine(
            config=config(),
            transcribe=backend.transcribe,
            capabilities=AsrCapabilities(streaming_native=True),
            model_id="a-name-that-says-nothing",
        )
        assert isinstance(engine, PassthroughEngine)
