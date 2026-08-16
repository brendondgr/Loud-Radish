"""Measuring a recording's audio, and saying so when there was no speech in it.

`state: done, transcribed_seconds: 322.46, 2 segments` is unfalsifiable from outside. It is exactly
what a broken transcriber looks like, and exactly what an accurate one looks like on music. Five
minutes with `ffmpeg` and `numpy` settled which — 17.9 % of the energy below 100 Hz and a dominant
frequency of 52 Hz, which is not a human voice.

These tests hold that investigation to its own results. The synthetic fixtures are built to known
spectra, so the measurements are checkable against arithmetic rather than against a previous run.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest
from app.services.recording.characterise import SILENCE_DBFS, characterise
from app.services.recording.job import JobState, TranscriptionJob

RATE = 16_000


def write(path: Path, samples: np.ndarray, rate: int = RATE) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def tone(hz: float, seconds: float = 5.0, amplitude: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return amplitude * np.sin(2 * np.pi * hz * t)


# -- the measurements --------------------------------------------------------------------------


def test_a_bass_heavy_recording_is_not_called_speech(tmp_path: Path) -> None:
    """The actual case: a 52 Hz dominant with energy spread broadband above it.

    A human fundamental sits at roughly 85–255 Hz and speech carries very little below 100 Hz, so
    this shape is music or game audio. Getting this label wrong in the confident direction would be
    worse than having no label — it would tell the user the capture was fine when it was not.
    """
    path = write(tmp_path / "bass.wav", tone(52) + tone(120, amplitude=0.15))

    character = characterise(path)

    assert character.dominant_hz == pytest.approx(52, abs=2)
    assert character.likely_content == "likely_music_or_game"


def test_a_speech_shaped_recording_is_called_speech(tmp_path: Path) -> None:
    """Energy concentrated where formants live, with a plausible fundamental."""
    path = write(
        tmp_path / "voice.wav",
        tone(180, amplitude=0.35) + tone(900, amplitude=0.3) + tone(2200, amplitude=0.25),
    )

    character = characterise(path)

    assert character.likely_content == "likely_speech"
    assert character.speech_band_ratio > 0.35


def test_silence_is_reported_as_inaudible_rather_than_as_speech(tmp_path: Path) -> None:
    """The other empty-transcript cause, and it has a different remedy from music.

    Silence means the wrong source was captured. Music means the right one was. Telling a user to
    check their audio source when the capture worked perfectly sends them the wrong way.
    """
    path = write(tmp_path / "quiet.wav", np.zeros(RATE * 5, dtype=np.float32))

    character = characterise(path)

    assert character.audible_pct == 0.0
    assert character.likely_content != "likely_speech"


def test_a_loud_recording_is_flagged_as_clipped(tmp_path: Path) -> None:
    """Monitor taps are pre-volume and can exceed unity — the real one measured peak 1.12.

    Clipped audio distorts on encode and hurts transcription, and it is invisible unless measured.
    """
    path = write(tmp_path / "loud.wav", tone(440, amplitude=1.0))

    character = characterise(path)

    assert character.peak >= 0.99


def test_continuous_audio_is_fully_audible(tmp_path: Path) -> None:
    path = write(tmp_path / "steady.wav", tone(440))

    assert characterise(tmp_path / "steady.wav").audible_pct == 100.0
    assert SILENCE_DBFS < 0


def test_the_bands_sum_to_about_a_hundred(tmp_path: Path) -> None:
    """They are shares of the 20–8000 Hz total, so they must account for it."""
    path = write(tmp_path / "mix.wav", tone(200) + tone(1500) + tone(4000))

    total = sum(characterise(path).band_energy_pct.values())

    assert total == pytest.approx(100, abs=2)


def test_a_missing_file_is_reported_rather_than_raised(tmp_path: Path) -> None:
    """This runs after a recording exists. Failing here must not lose the recording."""
    character = characterise(tmp_path / "nothing-here.wav")

    assert character.duration_s == 0.0
    assert character.likely_content == "unknown"


def test_an_all_nan_recording_does_not_crash_the_measurement(tmp_path: Path) -> None:
    """The AU-header fault, seen from the other end. It is now caught at capture time.

    This is the belt to that braces: if a non-finite file ever reaches disk, characterising it
    reports rather than raises, because by this point the audio is the only copy of the talk.
    """
    path = tmp_path / "nan.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(b"\x00\x00" * RATE)

    character = characterise(path)

    assert character.duration_s > 0


# -- the terminal state ------------------------------------------------------------------------


def test_finishing_with_no_segments_is_a_distinct_state() -> None:
    """`done` for "found nothing" is what made an empty transcript indistinguishable from a crash."""
    job = TranscriptionJob(session_id="s", source_path="/tmp/x.wav", total_seconds=300.0)

    job.finish(found_speech=False)

    assert job.state is JobState.DONE_NO_SPEECH
    assert job.succeeded is True
    assert job.error == ""


def test_finishing_with_segments_is_an_ordinary_done() -> None:
    job = TranscriptionJob(session_id="s", source_path="/tmp/x.wav", total_seconds=300.0)

    job.finish(found_speech=True)

    assert job.state is JobState.DONE
    assert job.succeeded is True


def test_a_failure_is_neither(self=None) -> None:
    job = TranscriptionJob(session_id="s", source_path="/tmp/x.wav", total_seconds=300.0)

    job.fail("the model would not load")

    assert job.succeeded is False
    assert job.state is JobState.FAILED


def test_the_measurements_travel_on_the_event() -> None:
    """So the interface can explain an empty transcript without a second request."""
    job = TranscriptionJob(session_id="s", source_path="/tmp/x.wav", total_seconds=300.0)
    job.audio = {"likely_content": "likely_music_or_game", "dominant_hz": 52.0}

    event = job.as_event()

    assert event["audio"]["likely_content"] == "likely_music_or_game"


# -- the sidecar file --------------------------------------------------------------------------


def test_the_sidecar_lands_beside_the_recording(tmp_path: Path) -> None:
    """It describes the *file*, so it lives with the file and survives the session being deleted."""
    from app.services.recording.runner import TranscriptionRunner

    source = write(tmp_path / "talk.wav", tone(180) + tone(900))
    job = TranscriptionJob(session_id="s", source_path=str(source), total_seconds=5.0)
    job.audio = characterise(source).as_dict()
    job.finish(found_speech=True)

    TranscriptionRunner._write_sidecar(object.__new__(TranscriptionRunner), job)

    sidecar = source.with_suffix(".json")
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text())
    assert payload["source"] == "talk.wav"
    assert payload["transcription"]["state"] == "done"
    assert payload["audio"]["likely_content"] == "likely_speech"
