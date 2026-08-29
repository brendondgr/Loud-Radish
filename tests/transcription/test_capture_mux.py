"""Combining a recording's video with the audio captured alongside it.

Two properties this file exists to hold, both reported as faults against a real recording.

**One video file comes out, not two.** The mux used to keep the silent original for ever, so every
window recording stored the same footage twice — 27 MB where 13 would do. The caution was right and
only its threshold was wrong: the original must outlive a *failed* mux, not a successful one.

**The picture is aligned with the sound.** Audio capture starts before the screen-cast portal is
asked, deliberately, so the first words of a talk are not lost while someone chooses a window from
a dialog. The video therefore begins later, and lining both inputs up at zero plays the sound ahead
of the picture by exactly that gap.

These run against real `ffmpeg`, because the thing under test is an ffmpeg invocation and a test
that asserts the argv would have passed while the flag did nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import wave

import numpy as np
import pytest
from app.services.audio.formats import SAMPLE_RATE
from app.services.capture import mux

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are needed to combine two real files",
)


def write_video(path, seconds: float = 4.0):
    """A small real WebM, encoded the way the capture pipeline encodes."""
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=160x120:rate=10:duration={seconds}",
            "-c:v",
            "libvpx",
            "-b:v",
            "80k",
            str(path),
        ],
        check=True,
    )
    return path


def write_audio(path, seconds: float = 6.0):
    """A WAV in the canonical capture format, longer than the video — as a real one is."""
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    signal = (np.sin(2 * np.pi * 220 * t) * 0.4 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(signal.tobytes())
    return path


def stream_start(path, kind: str) -> float:
    """The first timestamp of one stream in the output, which is what alignment moves."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            kind[0],
            "-show_entries",
            "stream=start_time",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip().splitlines()[0])


# -- one file, not two -------------------------------------------------------------------------


def test_the_silent_original_is_removed_once_the_combined_file_is_verified(tmp_path) -> None:
    video = write_video(tmp_path / "video.webm")
    audio = write_audio(tmp_path / "audio.wav")

    result = mux.combine(video, audio)

    assert result.ok
    assert result.removed_source is True
    assert not video.exists(), "the same footage was stored twice"
    assert mux.has_both_streams(result.path)


def test_the_original_can_still_be_kept_deliberately(tmp_path) -> None:
    video = write_video(tmp_path / "video.webm")
    audio = write_audio(tmp_path / "audio.wav")

    result = mux.combine(video, audio, keep_sources=True)

    assert result.removed_source is False
    assert video.exists()


def test_a_file_with_only_a_video_track_does_not_count_as_combined(tmp_path) -> None:
    """The failure the verification exists to catch: a mux that dropped the audio silently."""
    video = write_video(tmp_path / "video.webm")

    assert mux.has_both_streams(video) is False


def test_a_missing_audio_file_leaves_the_video_alone(tmp_path) -> None:
    video = write_video(tmp_path / "video.webm")

    result = mux.combine(video, tmp_path / "absent.wav")

    assert not result.ok
    assert video.exists(), "a mux that could not run must not cost the recording"


# -- alignment ---------------------------------------------------------------------------------


def test_the_video_is_delayed_to_meet_the_audio(tmp_path) -> None:
    """The reported drift: the sound ran ahead of the picture by the capture-start gap."""
    video = write_video(tmp_path / "video.webm")
    audio = write_audio(tmp_path / "audio.wav")

    result = mux.combine(video, audio, video_lag_s=2.0)

    assert result.ok
    assert result.video_lag_s == pytest.approx(2.0)
    assert stream_start(result.path, "video") == pytest.approx(2.0, abs=0.15)
    # The audio keeps its own clock, because that is the clock the transcript is written in.
    assert stream_start(result.path, "audio") == pytest.approx(0.0, abs=0.05)


def test_no_offset_leaves_both_streams_where_they_were(tmp_path) -> None:
    video = write_video(tmp_path / "video.webm")
    audio = write_audio(tmp_path / "audio.wav")

    result = mux.combine(video, audio)

    assert result.video_lag_s == 0.0
    assert stream_start(result.path, "video") == pytest.approx(0.0, abs=0.05)


@pytest.mark.parametrize("lag", [0.0, 0.01, -3.0, 600.0])
def test_an_offset_outside_the_range_worth_applying_is_ignored(tmp_path, lag) -> None:
    """Below the measurement's own error, or large enough to be a fault rather than a figure."""
    video = write_video(tmp_path / "video.webm", seconds=2.0)
    audio = write_audio(tmp_path / "audio.wav", seconds=3.0)

    result = mux.combine(video, audio, video_lag_s=lag)

    assert result.ok
    assert result.video_lag_s == 0.0


def test_the_duration_probe_reads_a_real_file(tmp_path) -> None:
    """The other half of the offset measurement: a video's start is its stop minus its length."""
    video = write_video(tmp_path / "video.webm", seconds=3.0)

    assert mux.probe_duration(video) == pytest.approx(3.0, abs=0.2)


def test_the_duration_probe_reports_zero_for_something_it_cannot_read(tmp_path) -> None:
    """Zero means "do not correct", which is what an unmeasurable offset must fall back to."""
    junk = tmp_path / "notes.txt"
    junk.write_text("not a video")

    assert mux.probe_duration(junk) == 0.0
    assert mux.probe_duration(tmp_path / "absent.webm") == 0.0
