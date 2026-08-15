"""`recorded` mode end to end: capture without inference, transcribe on stop (D-021).

The two claims this mode makes are both testable, and both are the kind that fail quietly if they
are not:

1. **No inference happens while recording.** That is the reason to choose the mode — it is what
   lets a laptop record two hours of talk — and a wiring mistake that quietly ran the engine anyway
   would still produce a correct transcript, so nothing but a test would notice.
2. **The transcript arrives after the toggle, not during.** Which means the pass must outlive the
   session, keeping its store open while the manager tears everything else down.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.models.session import SessionMetadata
from app.services.audio.formats import SAMPLE_RATE
from app.services.session import SessionManager, modes

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, name: str, data: dict) -> None:
        with self._lock:
            self.events.append((name, data))

    def of(self, name: str) -> list[dict]:
        with self._lock:
            return [data for event, data in self.events if event == name]

    def wait_for(self, name: str, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.of(name):
                return True
            time.sleep(0.02)
        return False


def write_talk_wav(path: Path, seconds: float = 6.0) -> Path:
    """A syllable-modulated tone — realistic enough for the VAD to call it speech."""
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


@pytest.fixture
def manager(tmp_path: Path):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav")),
            "audio.file_speed": 0.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "recording.recording_dir": str(tmp_path / "recordings"),
            "recording.batch_window_s": 5.0,
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
        }
    )
    recorder = Recorder()
    manager = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")
    return manager, store, recorder, tmp_path


def recorded_session(**kwargs) -> SessionMetadata:
    import uuid

    return SessionMetadata(session_id=uuid.uuid4().hex[:12], mode=modes.RECORDED, **kwargs)


async def drive(manager, recorder, tmp_path: Path):
    """Run a full recorded session and wait for its transcription pass to finish."""
    await manager.start(recorded_session())
    # The file source is replaying as fast as it can; give it enough to reach the end.
    for _ in range(400):
        if manager.session_seconds > 0 or recorder.of("audio.level"):
            break
        time.sleep(0.01)
    time.sleep(0.6)
    await manager.stop()
    return recorder.wait_for("transcription.done")


# -- the mode's central claim -------------------------------------------------------------


async def test_nothing_is_transcribed_while_recording(manager) -> None:
    """The whole reason to choose this mode. A wiring slip here is invisible without a test."""
    session, _store, recorder, _tmp = manager
    await session.start(recorded_session())
    try:
        time.sleep(0.5)
        assert recorder.of("transcript.committed") == []
        assert recorder.of("transcript.hypothesis") == []
    finally:
        await session.stop()


async def test_the_engine_is_never_built(manager) -> None:
    session, _store, _recorder, _tmp = manager
    await session.start(recorded_session())
    try:
        assert session._engine is None, "recorded mode built a streaming engine"
    finally:
        await session.stop()


async def test_the_level_meter_still_works(manager) -> None:
    """Nothing is transcribed, but the interface must not look dead."""
    session, _store, recorder, _tmp = manager
    await session.start(recorded_session())
    try:
        assert recorder.wait_for("audio.level", timeout=3.0)
    finally:
        await session.stop()


# -- the file it writes ---------------------------------------------------------------------


async def test_a_recording_file_is_written(manager) -> None:
    session, _store, _recorder, tmp_path = manager
    await session.start(recorded_session())
    try:
        time.sleep(0.4)
        recordings = list((tmp_path / "recordings").glob("*.wav"))
        assert recordings, "no recording file was created"
        assert recordings[0].stat().st_size > 44, "only a header was written"
    finally:
        await session.stop()


async def test_the_recording_is_reported_in_the_session_state(manager) -> None:
    session, _store, _recorder, _tmp = manager
    await session.start(recorded_session())
    try:
        time.sleep(0.4)
        state = session.state()
        assert state["recording"] is not None
        assert state["recording"]["duration_s"] > 0
    finally:
        await session.stop()


async def test_live_mode_writes_no_recording(manager) -> None:
    """The file belongs to the mode, not to sessions in general."""
    session, _store, _recorder, tmp_path = manager
    await session.start(SessionMetadata(session_id="live0001", mode=modes.LIVE))
    try:
        time.sleep(0.3)
        assert session.state()["recording"] is None
    finally:
        await session.stop()
    assert not list((tmp_path / "recordings").glob("*.wav"))


# -- the pass, after the toggle ---------------------------------------------------------------


async def test_the_transcript_arrives_after_stopping(manager) -> None:
    session, _store, recorder, tmp_path = manager
    assert await drive(session, recorder, tmp_path), "the transcription pass never finished"

    committed = recorder.of("transcript.committed")
    assert committed, "the pass produced no transcript"
    assert [s["id"] for s in committed] == list(range(1, len(committed) + 1))
    assert all(s["text"].strip() for s in committed)


async def test_progress_is_published_and_completes(manager) -> None:
    session, _store, recorder, tmp_path = manager
    assert await drive(session, recorder, tmp_path)

    progress = recorder.of("transcription.progress")
    assert progress, "no progress was reported for a pass that can take half an hour"
    assert recorder.of("transcription.done")[-1]["progress"] == 1.0


async def test_the_pass_is_readable_from_the_session_state(manager) -> None:
    """A reload mid-pass must resume showing progress, not an unexplained empty transcript."""
    session, _store, recorder, tmp_path = manager
    await drive(session, recorder, tmp_path)
    assert session.state()["transcription"] is not None


async def test_the_audio_is_deleted_once_transcribed(manager) -> None:
    """Retention is off by default, and that promise predates this mode."""
    session, _store, recorder, tmp_path = manager
    assert await drive(session, recorder, tmp_path)
    assert not list((tmp_path / "recordings").glob("*.wav"))


async def test_the_audio_is_kept_when_retention_is_on(manager) -> None:
    session, store, recorder, tmp_path = manager
    store.update({"storage.retain_audio": True})
    assert await drive(session, recorder, tmp_path)
    assert list((tmp_path / "recordings").glob("*.wav"))


async def test_the_transcript_survives_on_disk(manager) -> None:
    session, _store, recorder, tmp_path = manager
    assert await drive(session, recorder, tmp_path)
    assert list((tmp_path / "sessions").glob("*.db")), "no session database was written"


# -- failure and lifecycle ----------------------------------------------------------------------


async def test_a_session_that_captured_nothing_starts_no_pass(tmp_path: Path) -> None:
    """An empty file left on disk is only ever confusing.

    Needs a source that genuinely delivers nothing — a start/stop pair against the ordinary fixture
    still captures a fraction of a second, because the file source begins emitting immediately.
    """
    silent = tmp_path / "empty.wav"
    with wave.open(str(silent), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"")

    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(silent),
            "audio.file_speed": 0.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "recording.recording_dir": str(tmp_path / "recordings"),
        }
    )
    recorder = Recorder()
    session = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")

    await session.start(recorded_session())
    await session.stop()

    assert recorder.of("transcription.progress") == []
    assert not list((tmp_path / "recordings").glob("*.wav")), "an empty recording was left behind"


async def test_shutdown_does_not_wait_out_a_running_pass(manager) -> None:
    """A server taking half an hour to exit is one nobody will let start automatically."""
    session, _store, recorder, tmp_path = manager
    await session.start(recorded_session())
    time.sleep(0.5)
    await session.stop()

    started = time.monotonic()
    await session.shutdown()
    assert time.monotonic() - started < 10.0


async def test_shutdown_does_not_close_a_store_the_pass_is_writing_to(manager) -> None:
    """Regression. `keep_store` was an argument to teardown, and `shutdown` reaches teardown by a
    second path that had no way to know a pass was running — so it closed the database underneath
    the runner and the pass died with "Cannot operate on a closed database". The flag is instance
    state now, held by whoever handed the store over."""
    session, _store, recorder, tmp_path = manager
    await session.start(recorded_session())
    time.sleep(0.5)
    await session.stop()
    await session.shutdown()

    failures = [
        event
        for event in recorder.of("transcription.failed")
        if "closed database" in event.get("error", "")
    ]
    assert not failures, f"shutdown closed the store underneath the pass: {failures}"


async def test_the_transcript_is_readable_while_the_pass_runs(manager) -> None:
    """A reload during a half-hour pass must show the segments already committed, not an empty
    page — so the store stays *readable* even though the runner owns closing it."""
    session, _store, recorder, tmp_path = manager
    await session.start(recorded_session())
    time.sleep(0.5)
    await session.stop()

    # Either the pass is still going, in which case the store must still be readable, or it has
    # already finished, in which case the reference must have been dropped.
    if session.jobs.is_busy:
        assert session.store is not None, "the transcript became unreadable mid-pass"
    recorder.wait_for("transcription.done")
