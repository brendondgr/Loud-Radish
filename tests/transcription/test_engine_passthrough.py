"""The streaming-native bypass path (BE §7.8).

The requirement is parity: a streaming-native backend skips the commit machinery entirely, but the
output contract must be identical, so nothing downstream can tell which engine produced an event.
Every test here is about that equivalence.
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
    HypothesisUpdate,
    PassthroughEngine,
    StreamingEngine,
    build_engine,
)

FRAME_SECONDS = 0.25
SILENT_FRAME = np.zeros(int(FRAME_SECONDS * SAMPLE_RATE), dtype=np.float32)

TALK = (
    "the central claim is that these operators commute only on a dense subspace. "
    "outside it the bracket simply is not defined. "
    "so let me set the counterexample up properly now. "
    "take the position operator on the half line and the dilation generator alongside."
)

STREAMING_NATIVE = AsrCapabilities(
    word_timestamps=True,
    streaming_native=True,
    accepts_prompt=True,
    max_audio_seconds=None,
)


def config(**overrides: object) -> StreamingConfig:
    settings: dict = {"step_s": 0.5, "min_buffer_s": 0.4, "max_segment_s": 30.0, **overrides}
    return StreamingConfig(**settings)


def make_passthrough(
    text: str = TALK, **script_kwargs: object
) -> tuple[PassthroughEngine, MockAsrBackend]:
    backend = MockAsrBackend(
        MockScript(words=text.split(), **script_kwargs),  # type: ignore[arg-type]
        capabilities=STREAMING_NATIVE,
        model_id="mock:streaming",
    )
    backend.load()
    engine = PassthroughEngine(
        config=config(),
        transcribe=backend.transcribe,
        capabilities=STREAMING_NATIVE,
        model_id=backend.model_id,
    )
    return engine, backend


def drive(engine, frames: int, speaking: bool = True, frame: np.ndarray | None = None) -> list:
    events: list = []
    for _ in range(frames):
        chunk = (
            frame if frame is not None else positional_audio(engine.session_seconds, FRAME_SECONDS)
        )
        events.extend(engine.add_audio(chunk, speaking=speaking))
    return events


def segments(events: list) -> list:
    return [event.segment for event in events if isinstance(event, CommittedSegment)]


class TestSelection:
    def test_a_streaming_native_backend_gets_the_bypass(self) -> None:
        engine = build_engine(
            config=config(),
            transcribe=lambda audio, prompt: None,  # type: ignore[arg-type,return-value]
            capabilities=STREAMING_NATIVE,
        )
        assert isinstance(engine, PassthroughEngine)

    def test_an_offline_backend_gets_the_full_machinery(self) -> None:
        engine = build_engine(
            config=config(),
            transcribe=lambda audio, prompt: None,  # type: ignore[arg-type,return-value]
            capabilities=AsrCapabilities(streaming_native=False),
        )
        assert isinstance(engine, StreamingEngine)


class TestOutputParity:
    def test_it_emits_the_same_committed_segment_events(self) -> None:
        engine, _ = make_passthrough()
        events = drive(engine, frames=60)
        assert segments(events)

    def test_segments_have_the_same_shape(self) -> None:
        """Same fields, same types — the transport layer cannot special-case either engine."""
        engine, _ = make_passthrough()
        segment = segments(drive(engine, frames=60))[0]
        payload = segment.as_event()

        assert set(payload) == {
            "id",
            "text",
            "start",
            "end",
            "wall_clock",
            "confidence",
            "model_id",
            "speaker",
        }

    def test_segment_ids_are_monotonic(self) -> None:
        engine, _ = make_passthrough()
        ids = [segment.id for segment in segments(drive(engine, frames=80))]
        assert ids == sorted(ids)
        assert ids == list(range(1, len(ids) + 1))

    def test_timestamps_are_session_absolute(self) -> None:
        engine, _ = make_passthrough()
        emitted = segments(drive(engine, frames=80))
        assert emitted
        assert emitted[-1].end > 1.0
        for earlier, later in zip(emitted, emitted[1:], strict=False):
            assert earlier.end <= later.start + 1e-3

    def test_the_model_id_is_recorded(self) -> None:
        engine, _ = make_passthrough()
        assert all(s.model_id == "mock:streaming" for s in segments(drive(engine, frames=60)))

    def test_metrics_are_reported_in_the_same_shape(self) -> None:
        engine, _ = make_passthrough()
        drive(engine, frames=60)
        payload = engine.metrics.as_dict()
        assert "rtf" in payload
        assert payload["committed_words"] > 0

    def test_no_word_is_emitted_twice(self) -> None:
        """The bypass consumes what it submits, so nothing should be re-transcribed."""
        engine, _ = make_passthrough("alpha beta gamma delta epsilon zeta eta theta")
        text = " ".join(s.text for s in segments(drive(engine, frames=80)))
        words = text.split()
        assert len(words) == len(set(words)), f"duplicated words in: {text}"


class TestBypassBehaviour:
    def test_there_is_never_a_hypothesis_tail(self) -> None:
        """Output is already stable, so nothing is tentative."""
        engine, _ = make_passthrough()
        drive(engine, frames=60)
        assert engine.hypothesis_text == ""

    def test_the_buffer_never_accumulates(self) -> None:
        """Everything submitted is consumed; a growing buffer would mean duplicated text."""
        engine, _ = make_passthrough()
        drive(engine, frames=100)
        assert engine.buffer_seconds < 1.0

    def test_silence_is_skipped_and_discarded(self) -> None:
        engine, backend = make_passthrough()
        drive(engine, frames=40, speaking=False, frame=SILENT_FRAME)
        assert backend.pass_count == 0
        assert engine.metrics.skipped_silence > 0
        assert engine.buffer_seconds < 1.0

    def test_audio_shorter_than_the_step_is_held(self) -> None:
        engine, backend = make_passthrough()
        engine.add_audio(positional_audio(0.0, FRAME_SECONDS))
        assert backend.pass_count == 0


class TestLifecycle:
    def test_flush_closes_the_open_segment(self) -> None:
        engine, _ = make_passthrough("words without a terminating full stop here")
        drive(engine, frames=20)
        assert segments(engine.flush())

    def test_flush_clears_a_displayed_hypothesis(self) -> None:
        engine, _ = make_passthrough()
        drive(engine, frames=20)
        events = engine.flush()
        assert all(not isinstance(e, HypothesisUpdate) or e.text == "" for e in events)

    def test_reset_returns_to_a_clean_state(self) -> None:
        engine, _ = make_passthrough()
        drive(engine, frames=40)
        engine.reset(first_segment_id=1)

        assert engine.session_seconds == 0.0
        assert engine.buffer_seconds == 0.0
        assert engine.next_segment_id == 1

    def test_a_model_swap_is_recorded(self) -> None:
        engine, _ = make_passthrough()
        drive(engine, frames=20)
        engine.set_model("parakeet:0.6b", STREAMING_NATIVE)
        assert all(s.model_id == "parakeet:0.6b" for s in segments(drive(engine, frames=60)))

    def test_config_updates_apply(self) -> None:
        engine, _ = make_passthrough()
        engine.update_config(config(step_s=1.0, max_segment_s=5.0))
        assert engine.next_segment_id == 1


class TestPromptWiring:
    def test_a_prompt_reaches_the_backend(self) -> None:
        backend = MockAsrBackend(
            MockScript(words=TALK.split()), capabilities=STREAMING_NATIVE, model_id="mock:streaming"
        )
        backend.load()
        engine = PassthroughEngine(
            config=config(),
            transcribe=backend.transcribe,
            capabilities=STREAMING_NATIVE,
            prompt_builder=PromptBuilder(AsrConfig(session_prompt="dilation generator")),
        )
        drive(engine, frames=20)
        assert any("dilation" in (prompt or "") for prompt in backend.prompts_seen)

    def test_no_prompt_when_the_backend_cannot_use_one(self) -> None:
        capabilities = AsrCapabilities(streaming_native=True, accepts_prompt=False)
        backend = MockAsrBackend(MockScript(words=TALK.split()), capabilities=capabilities)
        backend.load()
        engine = PassthroughEngine(
            config=config(),
            transcribe=backend.transcribe,
            capabilities=capabilities,
            prompt_builder=PromptBuilder(AsrConfig(session_prompt="ignored")),
        )
        drive(engine, frames=20)
        assert all(prompt is None for prompt in backend.prompts_seen)


def test_both_engines_transcribe_the_same_talk_to_the_same_words() -> None:
    """The parity that matters: identical input, identical committed text.

    The offline path reaches it through two-pass agreement and the native path directly, but the
    reader sees the same transcript either way.
    """
    offline_backend = MockAsrBackend(MockScript(words=TALK.split()))
    offline_backend.load()
    offline = StreamingEngine(
        config=config(retained_context_s=0.5),
        transcribe=offline_backend.transcribe,
        capabilities=offline_backend.capabilities,
    )

    native, _ = make_passthrough()

    offline_events = drive(offline, frames=120) + offline.flush()
    native_events = drive(native, frames=120) + native.flush()

    offline_text = " ".join(s.text for s in segments(offline_events)).split()
    native_text = " ".join(s.text for s in segments(native_events)).split()

    assert offline_text == native_text
    assert len(offline_text) == pytest.approx(len(TALK.split()), abs=2)
