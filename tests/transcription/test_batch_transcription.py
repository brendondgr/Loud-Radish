"""Transcribing a finished recording in one pass (D-021).

The property that matters most is not accuracy — that belongs to the model — but **that the
segments are indistinguishable from the live path's**. The store, the FTS index, export, citations,
and the polish pass all work on segments, and every one of them would break in a different way if a
recorded transcript had different ids, different timestamps, or different boundaries.

The second property is that word times are on the *recording's* timeline. The model returns times
relative to the array it was handed, and every chunk after the first is handed a different array.
A rebasing bug here does not fail loudly; it produces a transcript whose every citation points at
the wrong moment.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from app.services.asr.contract import AsrResult, WordToken
from app.services.audio.formats import SAMPLE_RATE
from app.services.recording import (
    BatchError,
    JobRegistry,
    JobState,
    TranscriptionJob,
    plan_recording,
    read_wav,
    transcribe_file,
)
from app.services.recording.sink import WavSink
from app.services.vad.energy import EnergyVad


def write_recording(path: Path, seconds: float = 10.0) -> Path:
    sink = WavSink(path)
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    sink.write((np.sin(2 * np.pi * 200 * t) * 0.4).astype(np.float32))
    sink.close()
    return path


@dataclass
class FakeAsr:
    """Emits one word per second of submitted audio, timed within the submitted array.

    Deliberately naive: it says nothing about the recording it came from, so any test that gets
    recording-relative times out of it proves the rebasing rather than the fake.
    """

    model_id: str = "fake/scripted"
    calls: list[float] = None  # type: ignore[assignment]
    punctuate_every: int = 3

    def __post_init__(self) -> None:
        self.calls = []

    def __call__(self, audio: np.ndarray, prompt: str | None = None) -> AsrResult:
        seconds = audio.size / SAMPLE_RATE
        self.calls.append(seconds)
        words = []
        for index in range(int(seconds)):
            text = f"word{index}"
            if (index + 1) % self.punctuate_every == 0:
                text += "."
            words.append(
                WordToken(text=text, start=float(index), end=float(index) + 0.9, confidence=0.9)
            )
        return AsrResult(words=words, model_id=self.model_id)


# -- reading the file --------------------------------------------------------------------


def test_a_written_recording_reads_back_at_the_right_duration(tmp_path: Path) -> None:
    samples, duration = read_wav(write_recording(tmp_path / "talk.wav", 4.0))
    assert samples.dtype == np.float32
    assert duration == pytest.approx(4.0, abs=0.01)


def test_a_missing_recording_says_which_one(tmp_path: Path) -> None:
    with pytest.raises(BatchError, match="no longer exists"):
        read_wav(tmp_path / "gone.wav")


def test_a_wrong_rate_recording_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "wrong.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44_100)
        handle.writeframes(b"\x00\x00" * 1000)
    with pytest.raises(BatchError, match="44100 Hz"):
        read_wav(path)


# -- planning the chunks ------------------------------------------------------------------
#
# The recordings here are continuous tones, which the energy detector hears as speech throughout —
# so every cut is the hard one at the cap, and the chunks behave like fixed windows with no overlap.
# Where a cut falls when there *is* a pause is covered in `test_pause_chunking.py`.


def plan(samples: np.ndarray, **options):
    return plan_recording(samples, detector=EnergyVad(), **options)


def test_chunks_cover_the_whole_recording() -> None:
    samples = np.zeros(int(10 * SAMPLE_RATE), dtype=np.float32)
    chunks = plan(samples, chunk_s=3.0)
    assert chunks[0].start_s == 0.0
    assert chunks[-1].end_s == pytest.approx(10.0)


def test_chunks_tile_the_recording_without_overlap() -> None:
    """No overlap and no gap: nothing is transcribed twice and nothing is skipped (D-062)."""
    samples = np.zeros(int(10 * SAMPLE_RATE), dtype=np.float32)
    chunks = plan(samples, chunk_s=4.0)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.start_s == pytest.approx(previous.end_s)


def test_no_chunk_is_longer_than_the_cap() -> None:
    samples = np.zeros(int(10 * SAMPLE_RATE), dtype=np.float32)
    # Silence throughout is one long pause, so every cut is inside it — at the cap, since the
    # pause's midpoint lies beyond it.
    chunks = plan(samples, chunk_s=4.0)
    assert all(chunk.duration_s <= 4.0 + 1e-6 for chunk in chunks)
    assert len(chunks) >= 3


def test_a_resume_keeps_only_the_chunks_not_yet_done() -> None:
    """The chunk that ends exactly at the resume point is the one that wrote the checkpoint."""
    samples = np.zeros(int(10 * SAMPLE_RATE), dtype=np.float32)
    whole = plan(samples, chunk_s=4.0)
    resumed = plan(samples, chunk_s=4.0, start_s=whole[1].end_s)
    assert [chunk.start_s for chunk in resumed] == [chunk.start_s for chunk in whole[2:]]


def test_an_empty_recording_produces_no_chunks() -> None:
    assert plan(np.zeros(0, dtype=np.float32), chunk_s=3.0) == []


# -- the segments it produces ------------------------------------------------------------


def test_segments_carry_ids_contiguous_from_the_first(tmp_path: Path) -> None:
    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 9.0), transcribe=FakeAsr(), chunk_s=3.0
    )
    assert segments
    assert [s.id for s in segments] == list(range(1, len(segments) + 1))


def test_the_first_segment_id_can_be_offset(tmp_path: Path) -> None:
    """A window session may already hold a live transcript; ids must not collide."""
    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 6.0),
        transcribe=FakeAsr(),
        chunk_s=3.0,
        first_segment_id=50,
    )
    assert segments[0].id == 50


def test_word_times_are_on_the_recordings_timeline(tmp_path: Path) -> None:
    """The bug this catches is silent: every citation would point at the wrong moment."""
    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 12.0),
        transcribe=FakeAsr(),
        chunk_s=4.0,
    )
    assert segments[-1].end > 6.0, "late words were not rebased onto the recording's timeline"
    assert segments[-1].end <= 12.5


def test_segments_advance_in_time_and_do_not_overlap(tmp_path: Path) -> None:
    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 12.0), transcribe=FakeAsr(), chunk_s=4.0
    )
    for previous, current in zip(segments, segments[1:], strict=False):
        assert current.start >= previous.start
        assert previous.end <= current.end


def test_the_final_words_are_not_lost(tmp_path: Path) -> None:
    """A recording rarely ends on a full stop, and the end is where conclusions live."""
    unpunctuated = FakeAsr(punctuate_every=100)
    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 5.0), transcribe=unpunctuated, chunk_s=5.0
    )
    assert segments, "a recording with no sentence-ending punctuation produced nothing"
    assert "word4" in segments[-1].text


def test_the_model_id_reaches_the_segment(tmp_path: Path) -> None:
    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 4.0),
        transcribe=FakeAsr(model_id="whisper/small"),
        chunk_s=4.0,
    )
    assert segments[0].model_id == "whisper/small"


def test_a_silent_recording_produces_nothing_rather_than_failing(tmp_path: Path) -> None:
    """Empty results are what the hallucination filter returns for a suppressed pass."""
    sink = WavSink(tmp_path / "silence.wav")
    sink.write(np.zeros(SAMPLE_RATE * 4, dtype=np.float32))
    sink.close()

    def silent(audio: np.ndarray, prompt: str | None = None) -> AsrResult:
        return AsrResult(words=[], model_id="fake")

    assert transcribe_file(tmp_path / "silence.wav", transcribe=silent, chunk_s=2.0) == []


def test_an_empty_recording_is_not_an_error(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "empty.wav")
    sink.close()
    assert transcribe_file(tmp_path / "empty.wav", transcribe=FakeAsr()) == []


# -- progress ------------------------------------------------------------------------------


def test_progress_is_monotonic_and_reaches_the_end(tmp_path: Path) -> None:
    reported: list[float] = []
    transcribe_file(
        write_recording(tmp_path / "talk.wav", 12.0),
        transcribe=FakeAsr(),
        chunk_s=4.0,
        on_progress=lambda seconds, _segments: reported.append(seconds),
    )
    assert reported == sorted(reported), "progress went backwards"
    assert reported[-1] == pytest.approx(12.0, abs=0.1)


def test_progress_reports_the_segments_from_that_window(tmp_path: Path) -> None:
    batches: list[int] = []
    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 9.0),
        transcribe=FakeAsr(),
        chunk_s=3.0,
        on_progress=lambda _seconds, produced: batches.append(len(produced)),
    )
    assert sum(batches) == len(segments)


# -- stopping early --------------------------------------------------------------------------


def test_a_stop_request_returns_what_was_produced(tmp_path: Path) -> None:
    """A server shutdown must not wait for a forty-minute pass. Partial beats nothing."""
    calls = {"n": 0}

    def stop_after_two() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    segments = transcribe_file(
        write_recording(tmp_path / "talk.wav", 30.0),
        transcribe=FakeAsr(),
        chunk_s=3.0,
        should_stop=stop_after_two,
    )
    assert segments, "stopping early discarded everything already transcribed"
    assert segments[-1].end < 30.0


def test_stopping_immediately_still_flushes_nothing_rather_than_raising(tmp_path: Path) -> None:
    assert (
        transcribe_file(
            write_recording(tmp_path / "talk.wav", 5.0),
            transcribe=FakeAsr(),
            should_stop=lambda: True,
        )
        == []
    )


# -- the pass sees whole chunks, not frames ---------------------------------------------------


def test_the_model_is_given_long_chunks_not_frames(tmp_path: Path) -> None:
    """The point of the mode: full context per pass, rather than the live path's short buffers."""
    asr = FakeAsr()
    transcribe_file(write_recording(tmp_path / "talk.wav", 20.0), transcribe=asr, chunk_s=10.0)
    assert asr.calls
    assert max(asr.calls) == pytest.approx(10.0, abs=0.1)


# -- the job's state ---------------------------------------------------------------------------


class TestJob:
    """Progress is reported by audio position — honest, monotonic, no instrumentation needed."""

    def make(self, total: float = 100.0) -> TranscriptionJob:
        return TranscriptionJob(session_id="abc", source_path="/tmp/talk.wav", total_seconds=total)

    def test_progress_starts_at_zero_and_ends_at_one(self) -> None:
        job = self.make()
        assert job.progress == 0.0
        job.finish()
        assert job.progress == 1.0
        assert job.state is JobState.DONE

    def test_progress_never_walks_backwards(self) -> None:
        # A stale report must not be allowed to walk the figure back.
        job = self.make()
        job.advance(40.0, 2)
        job.advance(35.0, 1)
        assert job.transcribed_seconds == 40.0
        assert job.segments_written == 3

    def test_progress_is_clamped_past_the_end(self) -> None:
        """A final window may overrun the file's nominal length."""
        job = self.make(10.0)
        job.advance(12.0, 1)
        assert job.progress == 1.0

    def test_a_zero_length_recording_does_not_divide_by_zero(self) -> None:
        job = self.make(0.0)
        assert job.progress == 0.0
        job.finish()
        assert job.progress == 1.0

    def test_a_failure_names_the_recording_that_survived_it(self) -> None:
        job = self.make()
        job.fail("the model would not load")
        assert job.state is JobState.FAILED
        assert job.as_event()["error"] == "the model would not load"
        assert job.source_path.endswith("talk.wav")

    def test_the_event_payload_carries_what_the_interface_draws(self) -> None:
        job = self.make(60.0)
        job.advance(30.0, 4)
        event = job.as_event()
        assert event["progress"] == 0.5
        assert event["segments"] == 4
        assert event["total_seconds"] == 60.0


class TestJobRegistry:
    """One pass at a time. Two contending for one model on one device finish later than two in
    sequence, while also making the progress figure meaningless."""

    def test_a_second_pass_is_refused_while_one_runs(self) -> None:
        registry = JobRegistry()
        first = TranscriptionJob(session_id="a", source_path="a.wav", total_seconds=10)
        second = TranscriptionJob(session_id="b", source_path="b.wav", total_seconds=10)

        assert registry.claim(first) is True
        assert registry.claim(second) is False
        assert registry.current is first

    def test_the_slot_frees_when_a_pass_finishes(self) -> None:
        registry = JobRegistry()
        first = TranscriptionJob(session_id="a", source_path="a.wav", total_seconds=10)
        registry.claim(first)
        first.finish()

        second = TranscriptionJob(session_id="b", source_path="b.wav", total_seconds=10)
        assert registry.claim(second) is True

    def test_a_failed_pass_also_frees_the_slot(self) -> None:
        registry = JobRegistry()
        first = TranscriptionJob(session_id="a", source_path="a.wav", total_seconds=10)
        registry.claim(first)
        first.fail("disk full")
        assert registry.claim(
            TranscriptionJob(session_id="b", source_path="b.wav", total_seconds=10)
        )

    def test_a_finished_pass_stays_readable_until_cleared(self) -> None:
        """So a page reloaded after the pass ended still sees that it ended, and how."""
        registry = JobRegistry()
        job = TranscriptionJob(session_id="a", source_path="a.wav", total_seconds=10)
        registry.claim(job)
        job.finish()

        assert registry.current is job
        assert registry.is_busy is False
        registry.clear()
        assert registry.current is None

    def test_clearing_a_running_pass_does_nothing(self) -> None:
        registry = JobRegistry()
        job = TranscriptionJob(session_id="a", source_path="a.wav", total_seconds=10)
        registry.claim(job)
        registry.clear()
        assert registry.current is job
