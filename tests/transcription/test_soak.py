"""An accelerated soak run (BE §19.2).

A ninety-minute talk is what this application exists to survive, and the failures that only appear
over ninety minutes are exactly the ones a unit test cannot reach: a buffer that grows without
bound, a segment id that repeats after a few thousand commits, a clock that drifts a little on every
iteration until every citation lands in the wrong place.

This drives hours of transcript through the engine in seconds — the mock backend and
position-encoded audio make the whole thing deterministic — and asserts the properties that must
hold at minute ninety exactly as they do at minute one:

* **memory is bounded** — the buffer never exceeds its configured ceiling, however long the talk;
* **committed text is immutable** — a segment is committed once and never revised (constraint C4);
* **time stays honest** — segment times track audio consumed, with no accumulated drift.

It is **not** the same as ninety wall-clock minutes, and ``docs/checklist.md`` says so plainly. What
it does establish is that nothing here is a function of how many iterations have run.
"""

from __future__ import annotations

import numpy as np
from app.config.schema import StreamingConfig
from app.models.segment import Segment
from app.services.asr import MockAsrBackend, MockScript, positional_audio
from app.services.audio.formats import SAMPLE_RATE
from app.services.streaming import CommittedSegment, StreamingEngine

#: Ninety minutes of audio. Long enough that anything unbounded is unmissable.
SOAK_SECONDS = 90 * 60

#: One frame per push, matching the capture layer's default.
FRAME_SECONDS = 0.25

#: Long enough that the engine is never starved of new words to commit over ninety minutes.
SCRIPT_WORDS = 20_000

#: Tolerance for timestamp comparisons, in seconds.
#:
#: One millisecond is sixteen samples at 16 kHz, and is bounded below by the *test instrument*
#: rather than by the engine: the mock encodes each sample's position in a float32, which resolves
#: to roughly 60 µs at these magnitudes. A tighter epsilon measures that rounding rather than any
#: property of the code — and a millisecond is three orders of magnitude finer than anything that
#: could misplace a citation.
TIME_EPSILON = 1e-3


def build() -> tuple[StreamingEngine, StreamingConfig]:
    config = StreamingConfig(
        agreement_count=2,
        step_s=0.5,
        min_buffer_s=0.4,
        max_buffer_s=12.0,
        commit_timeout_s=8.0,
        retained_context_s=0.5,
    )
    backend = MockAsrBackend(
        MockScript(words=[f"word{index}" for index in range(SCRIPT_WORDS)], words_per_second=3.0)
    )
    backend.load()
    return StreamingEngine(config=config, transcribe=backend.transcribe, model_id="mock:soak"), (
        config
    )


def run(engine: StreamingEngine, seconds: float, config: StreamingConfig):
    """Drive ``seconds`` of position-encoded speech through the engine, checking bounds as it goes.

    Returns the committed segments, the peak buffer occupancy, and how much audio was supplied.
    """
    committed: list[Segment] = []
    peak = 0.0
    position = 0.0

    while position < seconds:
        events = engine.add_audio(positional_audio(position, FRAME_SECONDS), speaking=True)
        position += FRAME_SECONDS

        peak = max(peak, engine.buffer_seconds)
        assert engine.buffer_seconds <= config.max_buffer_s + FRAME_SECONDS, (
            f"buffer exceeded its ceiling at {position:.0f}s: "
            f"{engine.buffer_seconds:.1f}s > {config.max_buffer_s}s"
        )

        committed.extend(event.segment for event in events if isinstance(event, CommittedSegment))

    return committed, peak, position


def test_ninety_minutes_stays_bounded_and_append_only() -> None:
    engine, config = build()
    committed, peak, position = run(engine, SOAK_SECONDS, config)

    assert committed, "ninety minutes of speech produced no committed text at all"

    # C4: committed text is append-only. A segment committed twice, or revised after the fact, is
    # the single failure the whole commit policy exists to prevent.
    ids = [segment.id for segment in committed]
    assert len(ids) == len(set(ids)), "a segment was committed more than once"
    assert ids == sorted(ids), "segment ids must be issued in order"
    assert ids == list(range(ids[0], ids[0] + len(ids))), "segment ids must be contiguous"

    assert peak <= config.max_buffer_s + FRAME_SECONDS

    # The engine's clock must still agree with the audio it was handed after twenty-one thousand
    # iterations. Drift here would put every citation in the wrong place.
    assert abs(engine.session_seconds - position) < 0.5


def test_memory_does_not_grow_with_the_length_of_the_talk() -> None:
    """The property that makes a two-hour session possible at all (constraint C1).

    Compared between the first ten minutes and the last: an engine whose occupancy is a function of
    how long it has been running fails here even though every individual bound still holds.
    """
    engine, config = build()

    _, early_peak, _ = run(engine, 10 * 60, config)
    late_start = engine.session_seconds
    late_peak = 0.0
    position = late_start
    while position < late_start + 10 * 60:
        engine.add_audio(positional_audio(position, FRAME_SECONDS), speaking=True)
        position += FRAME_SECONDS
        late_peak = max(late_peak, engine.buffer_seconds)

    assert late_peak <= early_peak + 1.0, (
        f"buffer occupancy grew with session length: {early_peak:.1f}s early, {late_peak:.1f}s late"
    )


def test_timestamps_never_run_ahead_of_the_audio_supplied() -> None:
    """Position-encoded audio makes this checkable rather than merely plausible.

    The mock returns the words that genuinely fall inside the slice it was handed, so a segment
    landing at the wrong time means the engine's rebasing is wrong — not that the model was vague.
    """
    engine, config = build()

    last_end = 0.0
    position = 0.0
    while position < 30 * 60:
        events = engine.add_audio(positional_audio(position, FRAME_SECONDS), speaking=True)
        position += FRAME_SECONDS

        for segment in (e.segment for e in events if isinstance(e, CommittedSegment)):
            assert segment.start >= 0.0
            assert segment.end >= segment.start
            assert segment.start >= last_end - TIME_EPSILON, (
                f"segment at {segment.start:.4f}s overlaps one ending at {last_end:.4f}s"
            )
            assert segment.end <= position + TIME_EPSILON, (
                "a segment ended after audio that never arrived"
            )
            last_end = segment.end


def test_a_long_silence_mid_talk_does_not_accumulate() -> None:
    """A speaker pausing for ten minutes must not leave ten minutes of silence in the buffer."""
    engine, config = build()
    run(engine, 60.0, config)

    silence = np.zeros(int(SAMPLE_RATE * FRAME_SECONDS), dtype=np.float32)
    for _ in range(int(10 * 60 / FRAME_SECONDS)):
        engine.add_audio(silence, speaking=False)

    assert engine.buffer_seconds <= config.max_buffer_s + FRAME_SECONDS


def test_the_real_time_factor_stays_stable_rather_than_decaying() -> None:
    """A metric that degrades with session length would hide a pipeline slowly falling behind."""
    engine, config = build()

    run(engine, 5 * 60, config)
    early = engine.metrics.real_time_factor

    position = engine.session_seconds
    while position < 30 * 60:
        engine.add_audio(positional_audio(position, FRAME_SECONDS), speaking=True)
        position += FRAME_SECONDS
    late = engine.metrics.real_time_factor

    assert early > 0 and late > 0
    # Generous: this measures wall-clock on a shared machine. It is asserting "no collapse", not a
    # performance number, and a tight bound here would be a flaky test rather than a useful one.
    assert late > early * 0.25, f"real-time factor collapsed from {early:.1f} to {late:.1f}"
