"""The capture offset: how much later than the audio the video began, and how it is measured.

Reported as "the video and the audio are not synced up, one is behind the other a little bit". It
is not a fault in the recording hardware and not drift — it is a fixed offset present from the
first frame, and it comes from a deliberate decision in this application.

**Audio capture starts before the screen-cast portal is even asked.** That ordering is commented as
such in the session manager and it is right: the portal puts a dialog on screen and waits for a
human to choose a window, and starting the microphone afterwards would lose the first words of a
talk to however long they take. The consequence is that the video begins later than the audio, by
somewhere between a fraction of a second and the length of that pause — 2.1 s on the recording that
prompted this — and a mux that lines both inputs up at zero plays the sound that far ahead.

So the offset is measured and corrected rather than designed away. The measurement is indirect on
purpose: nothing observes the first encoded frame arriving, so the video's start is taken as the
moment it was told to stop minus its own encoded duration.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.services.recording import WavSink
from app.services.session import window_capture
from app.services.session.manager import SessionManager


def _sink_with_audio(tmp_path) -> WavSink:
    """A sink whose clock has started, which happens on the first frame and not before."""
    sink = WavSink(tmp_path / "audio.wav")
    sink.write(np.zeros(160, dtype=np.float32))
    return sink


def _manager(stopped_at: float) -> SessionManager:
    """A manager with only the field under test set. Constructing a real one opens a pipeline."""
    manager = SessionManager.__new__(SessionManager)
    manager._video_stopped_at = stopped_at
    return manager


def test_the_offset_is_the_gap_between_the_two_starts(tmp_path, monkeypatch) -> None:
    sink = _sink_with_audio(tmp_path)
    # The video ran for ten seconds and was stopped twelve seconds after the audio began, so it
    # began two seconds late — without anything ever having observed its first frame.
    monkeypatch.setattr(window_capture, "probe_video_duration", lambda path: 10.0)
    manager = _manager(sink.first_write_monotonic + 12.0)

    lag = SessionManager._measure_video_lag(manager, "video.webm", sink)

    assert lag == pytest.approx(2.0, abs=0.01)


def test_two_streams_that_started_together_need_no_correction(tmp_path, monkeypatch) -> None:
    sink = _sink_with_audio(tmp_path)
    monkeypatch.setattr(window_capture, "probe_video_duration", lambda path: 10.0)
    manager = _manager(sink.first_write_monotonic + 10.0)

    assert SessionManager._measure_video_lag(manager, "video.webm", sink) == pytest.approx(
        0.0, abs=0.01
    )


def test_an_unreadable_video_leaves_the_tracks_as_captured(tmp_path, monkeypatch) -> None:
    """An unmeasurable offset must never become a guessed one."""
    sink = _sink_with_audio(tmp_path)
    monkeypatch.setattr(window_capture, "probe_video_duration", lambda path: 0.0)
    manager = _manager(sink.first_write_monotonic + 12.0)

    assert SessionManager._measure_video_lag(manager, "video.webm", sink) == 0.0


def test_a_recorder_that_never_reported_stopping_leaves_the_tracks_alone(
    tmp_path, monkeypatch
) -> None:
    sink = _sink_with_audio(tmp_path)
    monkeypatch.setattr(window_capture, "probe_video_duration", lambda path: 10.0)

    assert SessionManager._measure_video_lag(_manager(0.0), "video.webm", sink) == 0.0


def test_a_sink_that_captured_nothing_leaves_the_tracks_alone(tmp_path, monkeypatch) -> None:
    """A sink that was opened but never written has no time zero to measure against."""
    sink = WavSink(tmp_path / "audio.wav")
    monkeypatch.setattr(window_capture, "probe_video_duration", lambda path: 10.0)

    assert SessionManager._measure_video_lag(_manager(1000.0), "video.webm", sink) == 0.0


# -- the sink's own clock ----------------------------------------------------------------------


def test_the_sinks_clock_starts_on_the_first_frame_not_on_opening(tmp_path) -> None:
    """The gap between opening the file and the first frame is not audio, and must not count.

    The sink is created before the audio source is started, so timing from construction would fold
    the source's own start-up into the offset and push the video that much too far.
    """
    sink = WavSink(tmp_path / "audio.wav")
    assert sink.first_write_monotonic == 0.0

    sink.write(np.zeros(160, dtype=np.float32))
    first = sink.first_write_monotonic
    assert first > 0.0

    sink.write(np.zeros(160, dtype=np.float32))
    assert sink.first_write_monotonic == first, "the clock must not restart on later frames"


def test_an_empty_frame_does_not_start_the_clock(tmp_path) -> None:
    sink = WavSink(tmp_path / "audio.wav")
    sink.write(np.zeros(0, dtype=np.float32))

    assert sink.first_write_monotonic == 0.0
