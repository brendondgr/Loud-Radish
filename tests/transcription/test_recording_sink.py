"""The capture sink — the one component in `recorded` mode that must not fail (D-021).

Everything downstream can be retried from the file it writes, and nothing can be retried without
one. So these tests are mostly about the file being *openable*, not about it being fast: a header
that says zero samples makes a recording unplayable however much audio follows it, and a crash that
leaves a corrupt file loses a talk that was successfully captured right up until the crash.

Every read-back here goes through the standard library's ``wave`` module rather than through our own
parsing. Checking our writer with our own reader would pass on a file no other program can open.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from app.services.audio.formats import SAMPLE_RATE
from app.services.recording import RecordingLimitReached, SinkError, WavSink


def tone(seconds: float, freq: float = 220.0, amplitude: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    return (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.float32)


def read_back(path: Path) -> tuple[int, int, int, np.ndarray]:
    """`(channels, sample_width, rate, samples)` as any other program would see the file."""
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        return (
            handle.getnchannels(),
            handle.getsampwidth(),
            handle.getframerate(),
            np.frombuffer(frames, dtype="<i2"),
        )


# -- the file is a real WAV -------------------------------------------------------------


def test_a_closed_recording_opens_as_a_standard_wav(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "talk.wav")
    sink.write(tone(1.0))
    sink.close()

    channels, width, rate, samples = read_back(tmp_path / "talk.wav")
    assert channels == 1
    assert width == 2
    assert rate == SAMPLE_RATE
    assert samples.size == SAMPLE_RATE


def test_duration_matches_what_was_written(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "talk.wav")
    for _ in range(4):
        sink.write(tone(0.5))
    sink.close()

    assert sink.duration_s == pytest.approx(2.0)
    _, _, _, samples = read_back(tmp_path / "talk.wav")
    assert samples.size == pytest.approx(2.0 * SAMPLE_RATE, abs=1)


def test_the_file_exists_and_opens_before_any_audio_arrives(tmp_path: Path) -> None:
    """A file that exists and cannot be opened is worse than one that does not exist yet."""
    sink = WavSink(tmp_path / "talk.wav")
    try:
        assert (tmp_path / "talk.wav").is_file()
        _, _, _, samples = read_back(tmp_path / "talk.wav")
        assert samples.size == 0
    finally:
        sink.close()


def test_the_header_is_current_while_recording_continues(tmp_path: Path) -> None:
    """The recording is openable mid-flight, which is how a user checks it is working at all."""
    sink = WavSink(tmp_path / "talk.wav")
    try:
        # Past the header-refresh interval, so a rewrite has definitely happened.
        for _ in range(4):
            sink.write(tone(1.0))
        _, _, _, samples = read_back(tmp_path / "talk.wav")
        # Not asserting the exact count: the header trails the writes by up to one interval by
        # design. What matters is that it is neither zero nor a lie about a longer file.
        assert 0 < samples.size <= 4 * SAMPLE_RATE
    finally:
        sink.close()


def test_a_killed_recording_is_short_rather_than_corrupt(tmp_path: Path) -> None:
    """Simulates a crash: frames written, never closed. The file must still open."""
    sink = WavSink(tmp_path / "talk.wav")
    for _ in range(3):
        sink.write(tone(1.0))
    # Deliberately no close() — this is the crash.
    del sink

    channels, width, rate, samples = read_back(tmp_path / "talk.wav")
    assert (channels, width, rate) == (1, 2, SAMPLE_RATE)
    assert samples.size > 0


# -- the audio itself is right -----------------------------------------------------------


def test_samples_survive_the_round_trip(tmp_path: Path) -> None:
    original = tone(0.25, freq=440.0, amplitude=0.5)
    sink = WavSink(tmp_path / "talk.wav")
    sink.write(original)
    sink.close()

    _, _, _, stored = read_back(tmp_path / "talk.wav")
    recovered = stored.astype(np.float32) / 32767.0
    # 16-bit quantisation is the only loss, and it is well under a thousandth.
    assert np.max(np.abs(recovered - original)) < 1e-3


def test_samples_over_full_scale_flatten_rather_than_wrap(tmp_path: Path) -> None:
    """Clipping before scaling, not after.

    Scaling first lets a sample slightly over 1.0 wrap to a large negative value — an audible click,
    indistinguishable from a hardware fault, where clipping produces inaudible flattening.
    """
    sink = WavSink(tmp_path / "hot.wav")
    sink.write(np.array([1.4, -1.4, 0.0, 2.0], dtype=np.float32))
    sink.close()

    _, _, _, stored = read_back(tmp_path / "hot.wav")
    assert stored.tolist() == [32767, -32767, 0, 32767]


def test_a_multidimensional_frame_is_flattened(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "talk.wav")
    sink.write(np.zeros((160, 1), dtype=np.float32))
    sink.close()
    _, _, _, samples = read_back(tmp_path / "talk.wav")
    assert samples.size == 160


# -- the duration cap ---------------------------------------------------------------------


def test_the_cap_stops_the_recording_and_says_so(tmp_path: Path) -> None:
    """A forgotten toggle must not fill the disk, and must not truncate in silence."""
    sink = WavSink(tmp_path / "capped.wav", max_minutes=1.0 / 60.0)  # one second

    assert sink.write(tone(0.4)) is False
    assert sink.write(tone(0.4)) is False
    assert sink.write(tone(0.4)) is True, "the cap was passed without being reported"
    sink.close()

    assert sink.duration_s == pytest.approx(1.0, abs=0.01)
    _, _, _, samples = read_back(tmp_path / "capped.wav")
    assert samples.size == SAMPLE_RATE


def test_audio_written_before_the_cap_is_intact(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "capped.wav", max_minutes=1.0 / 60.0)
    sink.write(np.full(SAMPLE_RATE // 2, 0.5, dtype=np.float32))
    sink.write(np.full(SAMPLE_RATE, 0.5, dtype=np.float32))
    sink.close()

    _, _, _, samples = read_back(tmp_path / "capped.wav")
    assert samples.size == SAMPLE_RATE
    assert np.all(samples == 16383) or np.all(np.abs(samples - 16383) <= 1)


def test_no_cap_means_no_cap(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "talk.wav", max_minutes=None)
    for _ in range(3):
        assert sink.write(tone(1.0)) is False
    sink.close()
    assert sink.duration_s == pytest.approx(3.0)


# -- failure and lifecycle -----------------------------------------------------------------


def test_an_unopenable_path_fails_at_construction(tmp_path: Path) -> None:
    """Fail where a caller can still do something about it, not on the first frame."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    with pytest.raises(SinkError, match="Could not open"):
        WavSink(blocker / "nested" / "talk.wav")


def test_closing_twice_is_harmless(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "talk.wav")
    sink.write(tone(0.1))
    assert sink.close() == sink.close()


def test_writing_after_close_is_ignored_rather_than_raising(tmp_path: Path) -> None:
    """A capture thread that raises kills the session; a late frame is not worth that."""
    sink = WavSink(tmp_path / "talk.wav")
    sink.write(tone(0.1))
    sink.close()
    assert sink.write(tone(0.1)) is True
    assert sink.duration_s == pytest.approx(0.1)


def test_discard_removes_the_file(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "throwaway.wav")
    sink.write(tone(0.1))
    sink.discard()
    assert not (tmp_path / "throwaway.wav").exists()


def test_the_parent_directory_is_created(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "deep" / "nested" / "talk.wav")
    sink.write(tone(0.1))
    sink.close()
    assert (tmp_path / "deep" / "nested" / "talk.wav").is_file()


def test_bytes_written_accounts_for_the_header(tmp_path: Path) -> None:
    sink = WavSink(tmp_path / "talk.wav")
    sink.write(tone(1.0))
    sink.close()
    assert sink.bytes_written == (tmp_path / "talk.wav").stat().st_size


def test_the_limit_error_is_a_sink_error() -> None:
    # The session manager catches SinkError broadly; a cap that escaped that would take down a
    # recording rather than ending it.
    assert issubclass(RecordingLimitReached, SinkError)
