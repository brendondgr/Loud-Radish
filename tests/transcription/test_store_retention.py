"""A finished session's transcript stays readable until the next one starts.

The reported fault: after stopping a transcription, asking the assistant to summarise the last ten
minutes answered *"There is no transcript to ask about yet"* — while the finished transcript was
plainly on screen. What is on screen is the client's own copy, accumulated over the WebSocket while
the session ran; it outlives the store, so the interface and the server disagreed about whether a
transcript existed at all.

Everything that reads a transcript reads it through ``SessionManager.store``: the assistant's
``store_provider``, both readers in ``routes/transcript.py``, and the stats in ``state()``. Teardown
closed it and set it to ``None``, so all of them were handed nothing the instant a session ended.

Three consequences are covered here, because one line caused all three: the assistant's refusal, an
empty transcript after a reload, and a clock of ``0.0`` that would make *"the last ten minutes"*
select the range ``[0, 0]`` and find nothing even with a store present.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.services.audio.formats import SAMPLE_RATE
from app.services.session import SessionManager


def _talk_wav(path: Path, seconds: float = 6.0) -> Path:
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
def anyio_backend() -> str:
    return "asyncio"


class Recorder:
    """Collects emitted transport events, so a test can wait for a real commit."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, name: str, data: dict) -> None:
        with self._lock:
            self.events.append((name, data))

    def wait_for(self, name: str, timeout: float = 6.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if any(event == name for event, _ in self.events):
                    return True
            time.sleep(0.02)
        return False


@pytest.fixture
def manager(tmp_path: Path) -> SessionManager:
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(_talk_wav(tmp_path / "talk.wav")),
            "audio.file_speed": 0.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
        }
    )
    recorder = Recorder()
    session = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")
    # Attached so a test can wait for real committed text before stopping. A session stopped the
    # instant it starts has an empty transcript for an entirely ordinary reason, which would make
    # these tests pass or fail on timing rather than on retention.
    session.recorder = recorder  # type: ignore[attr-defined]
    return session


async def _run_briefly(manager: SessionManager) -> None:
    """Start, wait for one committed segment, stop. The transcript is then non-empty."""
    await manager.start()
    try:
        assert manager.recorder.wait_for("transcript.committed"), "nothing was committed"
    finally:
        await manager.stop()


@pytest.mark.anyio
async def test_the_transcript_is_still_readable_after_the_session_stops(manager) -> None:
    """**The reported fault.** `store` was `None` the instant a session ended."""
    await manager.start()
    await manager.stop()

    assert manager.store is not None, "the finished transcript must still be readable"
    assert not manager.is_running, "retaining a store must not look like a running session"


@pytest.mark.anyio
async def test_the_finished_transcript_still_reports_its_segments(manager) -> None:
    """A reload re-fetches segments through the same store, and got an empty page without one."""
    await _run_briefly(manager)

    store = manager.store
    assert store is not None
    assert store.stats().segment_count > 0, "the finished session committed nothing to read back"


@pytest.mark.anyio
async def test_the_clock_survives_the_stop(manager) -> None:
    """Otherwise "the last ten minutes" resolves to ``[0, 0]`` and selects nothing.

    ``session_seconds`` already promised this in its own docstring — that it falls back to the
    stored transcript's duration once the engine is gone, "so a question asked after a session ends
    is still positioned correctly". The intent was written; closing the store defeated it.
    """
    await _run_briefly(manager)

    assert manager.session_seconds > 0.0


@pytest.mark.anyio
async def test_the_next_session_releases_the_previous_transcript(manager) -> None:
    """Exactly one is held. Otherwise every recording leaks a descriptor for the whole run."""
    await manager.start()
    await manager.stop()
    first = manager.store
    assert first is not None

    await manager.start()
    try:
        assert manager.store is not first, "the new session must read its own store"
    finally:
        await manager.stop()

    assert manager.store is not None
    assert manager.store is not first, "the previous transcript should not come back"


@pytest.mark.anyio
async def test_a_retained_transcript_is_closed_on_shutdown(manager) -> None:
    """A descriptor must not outlive the process."""
    await manager.start()
    await manager.stop()
    assert manager.store is not None

    await manager.shutdown()

    assert manager.store is None, "shutdown must not leave a transcript open"


def test_nothing_is_readable_before_anything_has_been_recorded(manager) -> None:
    """The genuinely empty case still reports empty.

    The assistant's "there is no transcript to ask about yet" is *correct* here, and this retention
    must not turn that honest message into a confusing one by inventing a store.
    """
    assert manager.store is None
