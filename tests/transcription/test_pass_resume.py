"""A transcription pass held, cancelled, and picked up again from its checkpoint (D-045).

The assertion that matters is the one Part 3g established as the right shape for this class of bug:
**every sentence appears exactly once**. A resume that begins a chunk too early re-decodes audio it
has already committed and says everything twice; one that begins a chunk too late drops whatever
lay between. Both look like a working transcript until somebody reads it.

Driven with a scripted backend over a real WAV, so the chunks, the seam and the segment
numbering are the real ones.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from app.models.segment import Segment
from app.models.session import SessionMetadata

# The real token, so `_absolute_words` runs against the shape it will see in production rather
# than one shaped to make this file pass.
from app.services.asr.contract import WordToken as Word  # noqa: E402
from app.services.audio.formats import SAMPLE_RATE
from app.services.recording.batch import plan_recording, transcribe_file
from app.services.recording.job import JobRegistry, JobState, TranscriptionJob
from app.services.recording.runner import TranscriptionRunner
from app.services.transcript.store import TranscriptStore
from app.services.vad.energy import EnergyVad


class Result:
    """What an ASR backend returns: words with times relative to the chunk it was given."""

    def __init__(self, words: list[Word]) -> None:
        self.words = words
        self.model_id = "scripted"
        self.text = " ".join(word.text for word in words)


def write_wav(path: Path, seconds: float) -> Path:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    signal = 0.3 * np.sin(2 * np.pi * 220 * t)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


def transcriber(offset_holder: dict):
    """One sentence per chunk, naming the second it started at, so the seam is readable."""

    def transcribe(samples: np.ndarray, prompt=None):  # noqa: ANN001, ANN202
        index = offset_holder["calls"]
        offset_holder["calls"] += 1
        duration = max(0.5, samples.size / SAMPLE_RATE)
        return Result(
            [
                Word(f"window{index}", 0.0, duration / 2),
                Word("spoken.", duration / 2, duration),
            ]
        )

    return transcribe


# -- the chunk planner ----------------------------------------------------------------------


def test_a_resume_starts_on_a_chunk_boundary() -> None:
    """Starting between two chunks would either re-decode audio already committed or skip the
    part the previous chunk had not reached."""
    samples = np.zeros(int(120 * SAMPLE_RATE), dtype=np.float32)

    chunks = plan_recording(samples, detector=EnergyVad(), chunk_s=30.0, start_s=47.0)

    assert chunks, "a resume inside the recording must produce chunks"
    # Silence throughout is one long pause, so every cut is at the cap: boundaries are 0, 30, 60,
    # 90 — and 47 s snaps back to 30.
    assert chunks[0].start_s == pytest.approx(30.0, abs=0.01)


def test_a_resume_past_the_end_produces_nothing() -> None:
    samples = np.zeros(int(10 * SAMPLE_RATE), dtype=np.float32)

    assert plan_recording(samples, detector=EnergyVad(), chunk_s=30.0, start_s=60.0) == []


def test_starting_at_zero_is_unchanged() -> None:
    samples = np.zeros(int(90 * SAMPLE_RATE), dtype=np.float32)

    plain = plan_recording(samples, detector=EnergyVad(), chunk_s=30.0)
    explicit = plan_recording(samples, detector=EnergyVad(), chunk_s=30.0, start_s=0.0)

    assert [c.start_s for c in plain] == [c.start_s for c in explicit]


def test_both_sides_of_a_resume_plan_the_same_boundaries() -> None:
    """What makes a checkpoint meaningful: the boundary it names exists on the next run too."""
    samples = np.zeros(int(100 * SAMPLE_RATE), dtype=np.float32)
    whole = plan_recording(samples, detector=EnergyVad(), chunk_s=30.0)
    later = plan_recording(samples, detector=EnergyVad(), chunk_s=30.0, start_s=whole[2].start_s)

    assert [c.start_s for c in later] == [c.start_s for c in whole[2:]]


# -- stopping and resuming the driver ---------------------------------------------------------


def test_a_stopped_pass_reports_where_it_got_to(tmp_path: Path) -> None:
    path = write_wav(tmp_path / "talk.wav", 120.0)
    holder = {"calls": 0}
    marks: list[tuple[float, int]] = []
    stop_after = 2

    segments = transcribe_file(
        path,
        transcribe=transcriber(holder),
        chunk_s=30.0,
        should_stop=lambda: len(marks) > stop_after,
        on_chunk_start=lambda start_s, next_id: marks.append((start_s, next_id)),
    )

    assert marks, "the checkpoint must be offered before each chunk"
    assert segments, "what was produced before stopping is returned, not discarded"
    assert marks[-1][0] > 0


def test_resuming_continues_the_segment_numbering(tmp_path: Path) -> None:
    """Ids must continue rather than restart, or the resumed half collides with what is already
    committed and the store ends up with two segments claiming the same id."""
    path = write_wav(tmp_path / "talk.wav", 120.0)
    holder = {"calls": 0}

    first = transcribe_file(
        path,
        transcribe=transcriber(holder),
        chunk_s=30.0,
        first_segment_id=1,
        should_stop=_after_windows(2),
    )
    resumed = transcribe_file(
        path,
        transcribe=transcriber(holder),
        chunk_s=30.0,
        first_segment_id=max(s.id for s in first) + 1,
        start_s=58.0,
    )

    ids = [s.id for s in first] + [s.id for s in resumed]
    assert len(ids) == len(set(ids)), f"segment ids repeat across the seam: {ids}"
    assert ids == sorted(ids)


def test_no_chunk_is_transcribed_twice_across_the_seam(tmp_path: Path) -> None:
    """The assertion Part 3g established for this class of bug: everything appears exactly once."""
    path = write_wav(tmp_path / "talk.wav", 120.0)
    holder = {"calls": 0}
    marks: list[float] = []

    first = transcribe_file(
        path,
        transcribe=transcriber(holder),
        chunk_s=30.0,
        should_stop=_after_windows(2),
        on_chunk_start=lambda start_s, _id: marks.append(start_s),
    )
    resume_at = marks[-1]
    resumed = transcribe_file(
        path,
        transcribe=transcriber({"calls": 100}),
        chunk_s=30.0,
        first_segment_id=max(s.id for s in first) + 1,
        start_s=resume_at,
    )

    # Each chunk covers a distinct span of the recording; the spans must tile it without overlap.
    starts = [round(s.start, 1) for s in first if s.start < resume_at]
    later = [round(s.start, 1) for s in resumed]
    assert not (set(starts) & set(later)), (
        f"a span was transcribed on both sides of the seam: {sorted(set(starts) & set(later))}"
    )


def _after_windows(count: int):
    """A `should_stop` that fires once `count` chunks have been started."""
    state = {"n": 0}

    def should_stop() -> bool:
        state["n"] += 1
        return state["n"] > count

    return should_stop


# -- the job's own states ----------------------------------------------------------------------


def test_a_job_can_be_held_and_let_go() -> None:
    job = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=60.0)

    job.pause()
    assert job.state is JobState.PAUSED
    assert job.is_resumable

    job.resume()
    assert job.state is JobState.RUNNING


def test_cancelling_is_not_failing() -> None:
    """An interface that showed a red error for a button the user pressed on purpose would be
    lying to them."""
    job = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=60.0)

    job.cancel()

    assert job.state is JobState.CANCELLED
    assert job.error == ""
    assert not job.is_resumable


def test_a_checkpoint_only_moves_forward() -> None:
    """A stale write must not walk the figure backwards — which on resume would
    re-decode audio already committed."""
    job = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=600.0)

    job.checkpoint(120.0, 40)
    job.checkpoint(90.0, 30)

    assert job.next_start_s == 120.0
    assert job.next_segment_id == 40


def test_the_event_says_whether_a_pass_can_be_picked_up() -> None:
    job = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=600.0)
    job.checkpoint(150.0, 12)
    job.pause()

    event = job.as_event()

    assert event["state"] == "paused"
    assert event["resumable"] is True
    assert event["next_start_s"] == 150.0


def test_a_held_pass_still_owns_the_slot() -> None:
    """Starting a second pass while the first waits to be resumed would make both slower and the
    progress figure meaningless, which is why there is only ever one."""
    registry = JobRegistry()
    first = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=60.0)
    assert registry.claim(first)
    first.pause()

    second = TranscriptionJob(session_id="b", source_path="/tmp/b.wav", total_seconds=60.0)

    assert registry.claim(second) is False


def test_a_held_pass_is_not_reported_as_busy() -> None:
    """Found by pressing Resume in the browser (D-045). `is_busy` guards "would starting something
    now collide", and a held pass answering yes made it refuse its own resumption with "a
    transcription is already running"."""
    registry = JobRegistry()
    job = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=60.0)
    registry.claim(job)

    assert registry.is_busy is True
    job.pause()
    assert registry.is_busy is False, "a held pass is not working, it is waiting"


def test_only_a_held_pass_can_be_displaced_by_a_resume() -> None:
    """A pass that is genuinely running is never displaced, whoever asks."""
    registry = JobRegistry()
    running = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=60.0)
    registry.claim(running)
    replacement = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=60.0)

    assert registry.claim(replacement, resuming=True) is False

    running.pause()
    assert registry.claim(replacement, resuming=True) is True


def test_a_cancelled_pass_releases_the_slot() -> None:
    registry = JobRegistry()
    first = TranscriptionJob(session_id="a", source_path="/tmp/a.wav", total_seconds=60.0)
    registry.claim(first)
    first.cancel()

    second = TranscriptionJob(session_id="b", source_path="/tmp/b.wav", total_seconds=60.0)

    assert registry.claim(second) is True


# -- the runner, end to end ---------------------------------------------------------------------


def test_holding_a_pass_writes_a_checkpoint_and_keeps_the_audio(tmp_path: Path) -> None:
    """The whole feature in one test: hold a running pass, and what is on disk afterwards is enough
    for a different process to pick it up."""
    audio = write_wav(tmp_path / "talk.wav", 150.0)
    database = tmp_path / "20260101-120000-aaaaaa.db"
    store = TranscriptStore(database, metadata=SessionMetadata(session_id="aaaaaa"))
    events: list[tuple[str, dict]] = []
    holder = {"calls": 0}

    runner = TranscriptionRunner(
        registry=JobRegistry(),
        emit=lambda name, data: events.append((name, data)),
        transcribe=_slow(transcriber(holder), runner_holder := {}),
        chunk_s=30.0,
    )
    runner_holder["runner"] = runner
    job = TranscriptionJob(session_id="aaaaaa", source_path=str(audio), total_seconds=150.0)

    assert runner.start(job=job, store=store, retain_audio=True)
    runner._thread.join(timeout=20)

    assert audio.is_file(), "a held pass must never delete the audio it has not finished reading"
    assert "transcription.paused" in [name for name, _ in events]

    reopened = TranscriptStore(database)
    try:
        checkpoint = reopened.resumable_pass()
        assert checkpoint is not None, "a different process must be able to find the checkpoint"
        assert checkpoint["state"] == "paused"
        assert checkpoint["source_path"] == str(audio)
        assert checkpoint["next_start_s"] > 0
    finally:
        reopened.close()


def _slow(transcribe, holder: dict):
    """Pause the runner from inside the second chunk, which is the only way to catch a pass that
    is genuinely mid-flight rather than one that never started."""
    state = {"n": 0}

    def wrapped(samples, prompt=None):  # noqa: ANN001, ANN202
        state["n"] += 1
        if state["n"] == 2 and (runner := holder.get("runner")) is not None:
            runner.pause()
        return transcribe(samples, prompt)

    return wrapped


def test_a_cancelled_pass_says_so_rather_than_failing(tmp_path: Path) -> None:
    audio = write_wav(tmp_path / "talk.wav", 90.0)
    store = TranscriptStore(tmp_path / "s.db", metadata=SessionMetadata(session_id="s"))
    events: list[tuple[str, dict]] = []
    holder: dict = {}
    runner = TranscriptionRunner(
        registry=JobRegistry(),
        emit=lambda name, data: events.append((name, data)),
        transcribe=_cancelling(transcriber({"calls": 0}), holder),
        chunk_s=30.0,
    )
    holder["runner"] = runner
    job = TranscriptionJob(session_id="s", source_path=str(audio), total_seconds=90.0)

    runner.start(job=job, store=store, retain_audio=True)
    runner._thread.join(timeout=20)

    names = [name for name, _ in events]
    assert "transcription.cancelled" in names
    assert "transcription.failed" not in names
    assert audio.is_file(), "cancelling declines the CPU, not the audio"


def _cancelling(transcribe, holder: dict):
    state = {"n": 0}

    def wrapped(samples, prompt=None):  # noqa: ANN001, ANN202
        state["n"] += 1
        if state["n"] == 1 and (runner := holder.get("runner")) is not None:
            runner.cancel()
        return transcribe(samples, prompt)

    return wrapped


def test_the_committed_segments_survive_a_cancel(tmp_path: Path) -> None:
    """Whatever was transcribed before the cancel is real transcript and stays."""
    audio = write_wav(tmp_path / "talk.wav", 150.0)
    database = tmp_path / "s.db"
    store = TranscriptStore(database, metadata=SessionMetadata(session_id="s"))
    store.append_segment(Segment(id=1, text="already committed", start=0.0, end=2.0))
    holder: dict = {}
    runner = TranscriptionRunner(
        registry=JobRegistry(),
        emit=lambda *_a: None,
        transcribe=_cancelling(transcriber({"calls": 0}), holder),
        chunk_s=30.0,
    )
    holder["runner"] = runner

    runner.start(
        job=TranscriptionJob(session_id="s", source_path=str(audio), total_seconds=150.0),
        store=store,
        retain_audio=True,
    )
    runner._thread.join(timeout=20)

    reopened = TranscriptStore(database)
    try:
        assert reopened.stats().segment_count >= 1
    finally:
        reopened.close()


def test_a_resume_trims_at_the_real_boundary_not_the_requested_one(tmp_path: Path) -> None:
    """Found on a real interrupted pass, not by reading (D-045).

    `plan_recording` snaps a resume back to the boundary at or before the requested second, so
    asking to resume at 540 s can genuinely begin at 522 s. Trimming at 540 and then transcribing
    from 522 re-derives 522-540 on top of segments that were kept — which produced overlapping
    timestamps and a line transcribed twice in the live run this test is taken from.
    """
    audio = write_wav(tmp_path / "talk.wav", 200.0)
    database = tmp_path / "s.db"
    store = TranscriptStore(database, metadata=SessionMetadata(session_id="s"))
    # Segments a previous run committed, including some inside the chunk a resume will snap into.
    for index, start in enumerate([100.0, 110.0, 118.0, 125.0], start=1):
        store.append_segment(
            Segment(id=index, text=f"committed at {start}", start=start, end=start + 4.0)
        )

    runner = TranscriptionRunner(
        registry=JobRegistry(),
        emit=lambda *_a: None,
        transcribe=transcriber({"calls": 0}),
        chunk_s=30.0,
    )
    job = TranscriptionJob(
        session_id="s", source_path=str(audio), total_seconds=200.0, next_start_s=130.0
    )

    runner.start(job=job, store=store, retain_audio=True)
    runner._thread.join(timeout=30)

    reopened = TranscriptStore(database)
    try:
        segments = reopened.latest_segments()
        starts = [round(s.start, 2) for s in segments]
        assert starts == sorted(starts), f"timestamps went backwards across the seam: {starts}"
        ids = [s.id for s in segments]
        assert len(ids) == len(set(ids)), f"segment ids repeat: {ids}"
        # A tone throughout gives the detector no pause, so every cut is at the 30 s cap and 120 is
        # the boundary at or before 130. Nothing committed at or after it survives.
        assert not [s for s in segments if s.start >= 120.0 and s.text.startswith("committed")], (
            "segments inside the re-derived chunk must be trimmed, not kept beside the new ones"
        )
        assert [s for s in segments if s.text.startswith("committed")], (
            "segments before the resume point must be left alone"
        )
    finally:
        reopened.close()


def test_a_pass_stopped_early_does_not_report_itself_complete(tmp_path: Path) -> None:
    """Found by pressing Pause and reading the label (D-045).

    The tail is flushed whether the loop finished or was stopped — partial transcript beats none —
    but reporting the whole file's length as the position walked progress to 100 % for a pass that
    had been *held*, which read as "Held at 00:39:11 of 00:39:11 — 100 %" over a pass at 65 %.
    """
    path = write_wav(tmp_path / "talk.wav", 200.0)
    positions: list[float] = []

    def unterminated(samples, prompt=None):  # noqa: ANN001, ANN202
        # No full stop, so the segmenter holds these words and the tail flush below actually runs.
        # With punctuation there is no tail, and the reporting path this test exists for is never
        # reached — which is how the first version of this test passed against the bug.
        duration = max(0.5, samples.size / SAMPLE_RATE)
        return Result(
            [Word("and then", 0.0, duration / 2), Word("he said", duration / 2, duration)]
        )

    transcribe_file(
        path,
        transcribe=unterminated,
        chunk_s=30.0,
        should_stop=_after_windows(2),
        on_progress=lambda seconds, _segments: positions.append(seconds),
    )

    assert positions, "progress must be reported at least once"
    assert max(positions) < 200.0, (
        f"a held pass reported the whole file as transcribed: {max(positions)}"
    )
