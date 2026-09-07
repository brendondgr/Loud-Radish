"""Holding a capture, letting it go again, and abandoning one (D-044).

The load-bearing claim is that **a pause removes time from the recording** rather than recording
silence. Everything else follows from it, and most of what is asserted here is that the three things
which must stop together — the audio file, the engine's clock, and the transcript's timestamps — do
stop together. Any one of them advancing while the others do not produces a recording whose
timestamps are wrong from the pause onward, which is a fault nobody would notice until they clicked
a citation.

Driven through the file source at full speed, so a "pause" here is a real gate on the real frame
path rather than a mock of one.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.models.session import SessionMetadata
from app.services.audio.formats import SAMPLE_RATE
from app.services.session import SessionError, SessionManager, modes

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def write_talk_wav(path: Path, seconds: float = 20.0) -> Path:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    tone = (
        0.45 * np.sin(2 * np.pi * 180 * t)
        + 0.28 * np.sin(2 * np.pi * 420 * t)
        + 0.14 * np.sin(2 * np.pi * 950 * t)
    )
    syllables = 0.12 + 0.88 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t))
    signal = np.clip(tone * syllables * 0.35, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


class Events:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict]] = []

    def __call__(self, name: str, data: dict) -> None:
        self.seen.append((name, data))

    def names(self) -> list[str]:
        return [name for name, _ in self.seen]


@pytest.fixture
def manager(tmp_path: Path):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav")),
            # Real time, so "how much audio arrived while paused" is a question with an answer.
            "audio.file_speed": 1.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "recording.recording_dir": str(tmp_path / "recordings"),
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
        }
    )
    events = Events()
    return (
        SessionManager(store, emit=events, session_dir=tmp_path / "sessions"),
        store,
        events,
    )


async def _run_briefly(manager: SessionManager, seconds: float) -> None:
    """Let real audio flow. The file source runs at 1.0x, so this is wall-clock audio."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.02)


# -- the state machine ---------------------------------------------------------------------


async def test_pausing_a_running_session_holds_it(manager) -> None:
    session, _store, events = manager
    await session.start()
    try:
        session.pause()

        assert session.is_paused is True
        assert session.is_running is True, "a held session still owns its device, file and store"
        assert session.state()["paused"] is True
        assert "session.paused" in events.names()
    finally:
        await session.stop()


async def test_resuming_continues_the_same_session(manager) -> None:
    session, _store, events = manager
    metadata = await session.start()
    try:
        session.pause()
        session.resume()

        assert session.is_paused is False
        assert session.state()["session"]["session_id"] == metadata.session_id
        assert "session.resumed" in events.names()
    finally:
        await session.stop()


async def test_pausing_twice_is_not_an_error(manager) -> None:
    """A second click on a control whose label has not repainted yet is a mistake worth absorbing,
    not one worth a banner."""
    session, _store, events = manager
    await session.start()
    try:
        session.pause()
        session.pause()

        assert session.is_paused is True
        assert len([n for n in events.names() if n == "session.paused"]) == 1
    finally:
        await session.stop()


async def test_resuming_a_session_that_is_not_held_does_nothing(manager) -> None:
    session, _store, events = manager
    await session.start()
    try:
        session.resume()

        assert "session.resumed" not in events.names()
    finally:
        await session.stop()


async def test_pausing_when_nothing_is_recording_is_refused(manager) -> None:
    session, _store, _events = manager

    with pytest.raises(SessionError):
        session.pause()


async def test_a_paused_session_is_not_paused_once_it_has_stopped(manager) -> None:
    session, _store, _events = manager
    await session.start()
    session.pause()
    await session.stop()

    assert session.is_paused is False, "nothing is held when nothing is running"


async def test_a_new_session_never_starts_held(manager) -> None:
    session, _store, _events = manager
    await session.start()
    session.pause()
    await session.stop()

    await session.start()
    try:
        assert session.is_paused is False
    finally:
        await session.stop()


# -- what actually stops -------------------------------------------------------------------


async def test_the_clock_does_not_advance_while_held(manager) -> None:
    """`session_seconds` is where every transcript timestamp comes from. If it moves during a hold,
    the transcript claims time in which nothing was said."""
    session, _store, _events = manager
    await session.start()
    try:
        await _run_briefly(session, 1.0)
        session.pause()
        held_at = session.session_seconds
        await _run_briefly(session, 1.5)

        assert session.session_seconds == pytest.approx(held_at, abs=0.05)
    finally:
        await session.stop()


async def test_the_clock_advances_again_after_a_resume(manager) -> None:
    session, _store, _events = manager
    await session.start()
    try:
        await _run_briefly(session, 0.8)
        session.pause()
        held_at = session.session_seconds
        await _run_briefly(session, 1.0)
        session.resume()
        await _run_briefly(session, 1.0)

        assert session.session_seconds > held_at + 0.4
    finally:
        await session.stop()


async def test_the_recording_stops_growing_while_held(tmp_path: Path) -> None:
    """The WAV and the clock must stop together, or the file is longer than the transcript that
    describes it and every timestamp after the pause is wrong."""
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav")),
            "audio.file_speed": 1.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "recording.recording_dir": str(tmp_path / "recordings"),
        }
    )
    session = SessionManager(store, emit=lambda *_a: None, session_dir=tmp_path / "sessions")
    # `recorded` mode, because that is the mode that writes a WAV — the file whose growth must stop
    # with the clock. In `live` there is nothing on disk to hold.
    await session.start(SessionMetadata(session_id="held0001", mode=modes.RECORDED))
    try:
        await _run_briefly(session, 0.8)
        session.pause()
        sink = session._sink
        assert sink is not None, "recorded mode must be writing a file"
        held_bytes = sink.bytes_written
        assert held_bytes > 0, "the recording must have started before the hold is meaningful"
        await _run_briefly(session, 1.2)

        assert sink.bytes_written == held_bytes
    finally:
        await session.stop()


async def test_the_level_meter_keeps_reporting_while_held(manager) -> None:
    """A paused session with a dead meter looks like a broken one. A moving meter says "we can
    still hear you, and we are not writing it down", which is exactly the state."""
    session, _store, events = manager
    await session.start()
    try:
        await _run_briefly(session, 0.5)
        session.pause()
        before = len([n for n in events.names() if n == "audio.level"])
        await _run_briefly(session, 1.0)

        assert len([n for n in events.names() if n == "audio.level"]) > before
    finally:
        await session.stop()


async def test_no_transcript_is_committed_while_held(manager) -> None:
    session, _store, events = manager
    await session.start()
    try:
        await _run_briefly(session, 1.0)
        session.pause()
        before = len([n for n in events.names() if n == "transcript.committed"])
        await _run_briefly(session, 2.0)

        assert len([n for n in events.names() if n == "transcript.committed"]) == before
    finally:
        await session.stop()


# -- cancelling ----------------------------------------------------------------------------


async def test_cancelling_keeps_what_was_captured(manager) -> None:
    """Cancel is a decision not to transcribe, not a decision to destroy. A control that discards
    a recording is one that will eventually discard the wrong one."""
    session, _store, events = manager
    await session.start()
    await _run_briefly(session, 0.6)

    await session.cancel()

    assert session.is_running is False
    assert "session.cancelled" in events.names()
    assert session.store is not None, "the transcript is still readable after a cancel"


async def test_cancelling_starts_no_transcription_pass(manager) -> None:
    session, _store, events = manager
    await session.start()
    await _run_briefly(session, 0.6)

    await session.cancel()

    assert "transcription.progress" not in events.names()
    assert session.jobs.current is None


async def test_cancelling_when_nothing_is_recording_is_refused(manager) -> None:
    session, _store, _events = manager

    with pytest.raises(SessionError):
        await session.cancel()


async def test_cancelling_a_held_session_works(manager) -> None:
    """Someone who has paused and then decided against the recording should not have to resume it
    first."""
    session, _store, events = manager
    await session.start()
    session.pause()

    await session.cancel()

    assert session.is_running is False
    assert "session.cancelled" in events.names()


async def test_stopping_normally_still_leaves_the_flag_clear(manager) -> None:
    """The cancel flag must not survive into the next session, or a stop would silently skip its
    pass for the rest of the process."""
    session, _store, _events = manager
    await session.start()
    await session.cancel()

    await session.start()
    try:
        assert session._cancelled is False
    finally:
        await session.stop()
