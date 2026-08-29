"""Asking the assistant about a talk that has finished.

**The reported fault, wired the way the application wires it.** After stopping a transcription,
pressing *"Summarise the last 10 minutes"* answered *"There is no transcript to ask about yet.
Start recording, then ask again."* — while the finished transcript was still on screen. The pane's
copy arrives over the WebSocket during the session and outlives the store, so the interface and the
server disagreed about whether a transcript existed.

These tests build the two objects the way ``app/main.py`` does — ``store_provider=lambda:
manager.store`` and ``clock=lambda: manager.session_seconds`` — because the fault lived in the
*seam*, not in either object. ``ChatService`` behaves correctly given a store, and the manager
recorded a perfectly good transcript; the provider handed over ``None`` the moment the session
ended, and nothing else had to be wrong for the question to be refused.
"""

from __future__ import annotations

import asyncio
import threading
import time
import wave
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.config.defaults import default_config
from app.services.audio.formats import SAMPLE_RATE
from app.services.chat import ChatError, ChatRequest, ChatService
from app.services.llm.contract import LlmChunk, LlmUsage
from app.services.session import SessionManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ScriptedBackend:
    """An LLM that answers with a fixed line, and remembers what it was asked."""

    provider_id = "local"
    endpoint = "http://localhost:9090/v1"

    def __init__(self) -> None:
        self.messages = None

    @property
    def model_id(self) -> str:
        return "default-model"

    async def _generate(self) -> AsyncIterator[LlmChunk]:
        yield LlmChunk(text="The speaker covered the dense subspace.")
        yield LlmChunk(done=True, usage=LlmUsage(10, 5), finish_reason="stop")

    def stream(self, messages, options=None):
        self.messages = messages
        return self._generate()


class Recorder:
    """Collects emitted events, as the hub would."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, name, payload) -> None:
        with self._lock:
            self.events.append((name, payload))

    def of(self, name):
        with self._lock:
            return [payload for event, payload in self.events if event == name]

    def text(self) -> str:
        return "".join(p.get("text", "") for p in self.of("chat.delta"))

    def wait_for(self, name: str, timeout: float = 6.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.of(name):
                return True
            time.sleep(0.02)
        return False


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
def wired(tmp_path: Path):
    """A session manager and an assistant, joined exactly as ``app/main.py`` joins them."""
    config = ConfigStore(config_path=tmp_path / "config.json")
    config.update(
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
    session_events = Recorder()
    manager = SessionManager(config, emit=session_events, session_dir=tmp_path / "sessions")

    chat_events = Recorder()
    backend = ScriptedBackend()
    chat = ChatService(
        store_provider=lambda: manager.store,
        config_provider=lambda: default_config(),
        backend_factory=lambda: backend,
        emit=chat_events,
        clock=lambda: manager.session_seconds,
    )
    return manager, session_events, chat, chat_events, backend


async def _record_and_stop(manager: SessionManager, events: Recorder) -> None:
    await manager.start()
    try:
        assert events.wait_for("transcript.committed"), "the session committed nothing"
    finally:
        await manager.stop()


async def _settle(chat: ChatService) -> None:
    if chat._task is not None:
        await asyncio.gather(chat._task, return_exceptions=True)


async def test_summarising_the_last_ten_minutes_after_a_stop(wired) -> None:
    """**The reported fault**, as the user met it: the quick action, after pressing stop."""
    manager, session_events, chat, chat_events, backend = wired
    await _record_and_stop(manager, session_events)

    await chat.ask(ChatRequest(action="summarise_10"))
    await _settle(chat)

    assert chat_events.text(), "the assistant answered nothing about a finished talk"
    assert not chat_events.of("chat.error"), chat_events.of("chat.error")


async def test_the_question_carries_the_finished_transcript(wired) -> None:
    """Answering is not enough — the transcript has to actually reach the model.

    Without this, a store that exists but yields no context would pass the test above while the
    assistant answered from nothing at all, which is the failure that looks most like success.
    """
    manager, session_events, chat, chat_events, backend = wired
    await _record_and_stop(manager, session_events)

    await chat.ask(ChatRequest(action="summarise_10"))
    await _settle(chat)

    sent = "\n".join(getattr(message, "content", "") for message in (backend.messages or []))
    assert sent.strip(), "no messages reached the model"
    stats = manager.store.stats() if manager.store else None
    assert stats is not None and stats.segment_count > 0
    assert "[" in sent, "the assembled context carried no timestamped transcript lines"


async def test_a_typed_question_after_a_stop_is_answered_too(wired) -> None:
    """Not only the quick actions — the same provider serves a typed question."""
    manager, session_events, chat, chat_events, _backend = wired
    await _record_and_stop(manager, session_events)

    await chat.ask(ChatRequest(message="what did the speaker argue?"))
    await _settle(chat)

    assert chat_events.text()


async def test_the_clock_positions_the_range_after_a_stop(wired) -> None:
    """*"The last ten minutes"* is measured from the clock, which fell to zero after a stop.

    A range of ``[0, 0]`` selects nothing, so the assistant would have answered from an empty
    context even once the store was retained — a second fault hidden behind the first.
    """
    manager, session_events, _chat, _chat_events, _backend = wired
    await _record_and_stop(manager, session_events)

    assert manager.session_seconds > 0.0


async def test_asking_before_anything_is_recorded_still_says_so(wired) -> None:
    """The honest empty case must survive the fix.

    "There is no transcript to ask about yet" is exactly right for an application that has never
    recorded, and retention must not replace it with an answer invented from nothing.
    """
    _manager, _session_events, chat, _chat_events, _backend = wired

    with pytest.raises(ChatError, match="no transcript to ask about yet"):
        await chat.ask(ChatRequest(action="summarise_10"))
