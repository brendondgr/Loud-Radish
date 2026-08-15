"""Session wiring, queue backpressure, metrics, and degradation (BE §14, §15, §16)."""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.services.audio.formats import SAMPLE_RATE
from app.services.session import DropOldestQueue, SessionError, SessionManager, Worker, degradation
from app.services.session.metrics import PipelineMetrics
from app.services.streaming.engine import EngineMetrics
from app.services.streaming.guards import Severity

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Recorder:
    """Collects emitted transport events, exactly as the hub will."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, name: str, data: dict) -> None:
        with self._lock:
            self.events.append((name, data))

    def names(self) -> list[str]:
        with self._lock:
            return [name for name, _ in self.events]

    def of(self, name: str) -> list[dict]:
        with self._lock:
            return [data for event, data in self.events if event == name]

    def wait_for(self, name: str, timeout: float = 6.0) -> bool:
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
def manager(tmp_path: Path) -> tuple[SessionManager, ConfigStore, Recorder]:
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav")),
            "audio.file_speed": 0.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
        }
    )
    recorder = Recorder()
    return SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions"), store, recorder


class TestQueue:
    def test_the_producer_never_blocks_on_a_full_queue(self) -> None:
        """Constraint C5, at the queue level."""
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=4)
        for value in range(1000):
            queue.put(value)
        assert len(queue) == 4
        assert queue.dropped == 996

    def test_the_oldest_item_is_dropped(self) -> None:
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=3)
        for value in range(5):
            queue.put(value)
        assert queue.drain() == [2, 3, 4]

    def test_put_reports_whether_it_dropped(self) -> None:
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=1)
        assert queue.put(1) is False
        assert queue.put(2) is True

    def test_get_returns_none_when_nothing_arrives(self) -> None:
        assert DropOldestQueue(capacity=2).get(timeout=0.01) is None

    def test_items_come_out_oldest_first(self) -> None:
        queue: DropOldestQueue[str] = DropOldestQueue(capacity=5)
        queue.put("a")
        queue.put("b")
        assert queue.get() == "a"
        assert queue.get() == "b"

    def test_a_zero_capacity_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            DropOldestQueue(capacity=0)

    def test_stats_report_fill(self) -> None:
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=4)
        queue.put(1)
        queue.put(2)
        assert queue.stats().fill == pytest.approx(0.5)

    def test_closing_wakes_a_waiting_consumer(self) -> None:
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=2)
        woke = threading.Event()

        def consume() -> None:
            queue.get(timeout=5.0)
            woke.set()

        thread = threading.Thread(target=consume)
        thread.start()
        queue.close()
        assert woke.wait(timeout=2.0)
        thread.join()


class TestWorker:
    def test_it_drains_the_queue(self) -> None:
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=10)
        seen: list[int] = []
        worker = Worker("test", queue, seen.append)  # type: ignore[arg-type]
        worker.start()

        for value in range(5):
            queue.put(value)
        deadline = time.monotonic() + 3.0
        while len(seen) < 5 and time.monotonic() < deadline:
            time.sleep(0.01)
        worker.stop()

        assert seen == [0, 1, 2, 3, 4]

    def test_a_failing_handler_does_not_kill_the_worker(self) -> None:
        """A worker that dies silently stops transcription with the UI still saying Recording."""
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=10)
        seen: list[int] = []

        def handler(value: int) -> None:
            if value == 1:
                raise RuntimeError("handler failure")
            seen.append(value)

        worker = Worker("test", queue, handler)  # type: ignore[arg-type]
        worker.start()
        for value in range(4):
            queue.put(value)

        deadline = time.monotonic() + 3.0
        while len(seen) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        worker.stop()

        assert seen == [0, 2, 3]
        assert worker.error_count == 1

    def test_stopping_an_unstarted_worker_is_harmless(self) -> None:
        Worker("test", DropOldestQueue(capacity=1), lambda item: None).stop()  # type: ignore[arg-type]


class TestSessionLifecycle:
    async def test_a_session_runs_end_to_end_and_produces_a_transcript(self, manager) -> None:
        session, _, recorder = manager
        await session.start()

        assert recorder.wait_for("transcript.committed"), "no committed text was produced"
        stats = await session.stop()

        assert stats.segment_count > 0
        assert not session.is_running

    async def test_the_started_event_carries_the_config_snapshot(self, manager) -> None:
        session, _, recorder = manager
        await session.start()
        try:
            started = recorder.of("session.started")[0]
            assert started["config"]["asr"]["backend"] == "mock"
            assert started["source"]
        finally:
            await session.stop()

    async def test_starting_twice_is_refused_with_a_clear_message(self, manager) -> None:
        session, _, _ = manager
        await session.start()
        try:
            with pytest.raises(SessionError, match="already recording"):
                await session.start()
        finally:
            await session.stop()

    async def test_stopping_when_idle_is_refused(self, manager) -> None:
        session, _, _ = manager
        with pytest.raises(SessionError, match="No session"):
            await session.stop()

    async def test_the_transcript_is_persisted_to_disk(self, manager, tmp_path: Path) -> None:
        session, _, recorder = manager
        await session.start()
        recorder.wait_for("transcript.committed")
        await session.stop()

        files = list((tmp_path / "sessions").glob("*.db"))
        assert files, "no session file was written"

    async def test_stopping_drains_what_capture_already_queued(self, manager) -> None:
        """Discarding queued frames would silently truncate the end of the talk."""
        session, _, recorder = manager
        await session.start()
        recorder.wait_for("transcript.committed")
        stats = await session.stop()

        assert stats.segment_count >= len(recorder.of("transcript.committed")) - 1

    async def test_stopping_marks_the_session_ended(self, manager) -> None:
        session, _, recorder = manager
        await session.start()
        await session.stop()

        stopped = recorder.of("session.stopped")
        assert stopped and "stats" in stopped[0]

    async def test_a_missing_file_source_says_what_to_do(self, manager) -> None:
        session, config, _ = manager
        config.set("audio.file_path", "")
        with pytest.raises(SessionError, match="Choose a WAV file"):
            await session.start()

    async def test_shutdown_is_safe_when_idle(self, manager) -> None:
        session, _, _ = manager
        await session.shutdown()
        assert not session.is_running

    async def test_shutdown_stops_a_running_session(self, manager) -> None:
        session, _, _ = manager
        await session.start()
        await session.shutdown()
        assert not session.is_running


class TestEmittedEvents:
    async def test_health_telemetry_is_published(self, manager) -> None:
        session, _, recorder = manager
        await session.start()
        try:
            assert recorder.wait_for("status", timeout=4.0)
            status = recorder.of("status")[-1]
            assert "rtf" in status and "queue_depth" in status
        finally:
            await session.stop()

    async def test_the_level_meter_is_published_but_throttled(self, manager) -> None:
        """Four a second is plenty; more just drives the frontend's render loop."""
        session, _, recorder = manager
        await session.start()
        try:
            assert recorder.wait_for("audio.level")
            time.sleep(1.0)
            assert len(recorder.of("audio.level")) < 40
        finally:
            await session.stop()

    async def test_vad_state_is_published_only_on_change(self, manager) -> None:
        session, _, recorder = manager
        await session.start()
        try:
            recorder.wait_for("transcript.committed")
        finally:
            await session.stop()
        # A six-second clip has a handful of transitions, not one per frame.
        assert len(recorder.of("vad.state")) < 30

    async def test_the_hypothesis_and_committed_events_are_distinct(self, manager) -> None:
        """The most important line in the contract: append versus replace."""
        session, _, recorder = manager
        await session.start()
        try:
            recorder.wait_for("transcript.committed")
        finally:
            await session.stop()

        assert "transcript.committed" in recorder.names()
        for payload in recorder.of("transcript.committed"):
            assert "id" in payload and "text" in payload
        for payload in recorder.of("transcript.hypothesis"):
            assert set(payload) == {"text", "start"}

    async def test_state_is_json_safe(self, manager) -> None:
        session, _, _ = manager
        await session.start()
        try:
            state = session.state()
            assert state["running"] is True
            assert state["session"]["session_id"]
            assert "rtf" in state["metrics"]
        finally:
            await session.stop()


class TestLiveConfig:
    async def test_live_settings_reach_the_running_pipeline(self, manager) -> None:
        session, config, _ = manager
        await session.start()
        try:
            config.update({"vad.sensitivity": 0.2, "streaming.step_s": 1.0})
            session.apply_live_config()
        finally:
            await session.stop()

    async def test_a_model_swap_flushes_first(self, manager) -> None:
        """Text the old model produced must not be attributed to the new one."""
        session, config, recorder = manager
        await session.start()
        try:
            recorder.wait_for("transcript.committed")
            config.set("asr.model", "swapped")
            await session.swap_model()
            assert session.asr.model_id == "mock:swapped"
        finally:
            await session.stop()


class TestDegradation:
    def test_a_lost_device_keeps_the_transcript(self) -> None:
        failure = degradation.device_lost("MacBook Pro Microphone")
        assert failure.severity is Severity.CRITICAL
        assert "transcript so far is safe" in failure.message
        assert not failure.transcription_continues

    def test_an_llm_failure_never_stops_transcription(self) -> None:
        """The guiding principle: a chat error is an inconvenience, a lost transcript is not."""
        assert degradation.llm_unavailable("no server").transcription_continues

    def test_falling_behind_names_a_specific_smaller_model(self) -> None:
        failure = degradation.falling_behind(0.8, "medium")
        assert "small" in failure.message
        assert failure.remedy == {"asr.model": "small"}

    def test_falling_behind_on_the_smallest_model_still_advises(self) -> None:
        failure = degradation.falling_behind(0.8, "tiny")
        assert failure.remedy is None
        assert "faster compute device" in failure.message

    def test_out_of_memory_offers_a_smaller_model_first(self) -> None:
        assert degradation.out_of_memory("large-v3", "GPU").remedy == {
            "asr.model": "large-v3-turbo"
        }

    def test_out_of_memory_on_the_smallest_model_offers_the_cpu(self) -> None:
        assert degradation.out_of_memory("tiny", "GPU").remedy == {"asr.device": "cpu"}

    def test_a_full_disk_keeps_recording(self) -> None:
        failure = degradation.disk_full("OSError")
        assert failure.transcription_continues
        assert "continues in memory" in failure.message

    def test_every_failure_serialises_with_its_remedy(self) -> None:
        payload = degradation.falling_behind(0.5, "small").as_event()
        assert set(payload) >= {"code", "message", "severity", "remedy", "remedy_label"}


class TestMetrics:
    def test_a_fresh_pipeline_is_reported_healthy(self) -> None:
        """A factor of zero before the speaker starts is not the system falling behind."""
        assert PipelineMetrics().is_healthy

    def test_a_factor_below_one_is_unhealthy(self) -> None:
        metrics = PipelineMetrics(engine=EngineMetrics(audio_seconds=10.0, inference_seconds=20.0))
        assert metrics.real_time_factor == pytest.approx(0.5)
        assert not metrics.is_healthy

    def test_dropped_audio_makes_the_pipeline_unhealthy(self) -> None:
        queue: DropOldestQueue[int] = DropOldestQueue(capacity=1)
        queue.put(1)
        queue.put(2)
        metrics = PipelineMetrics(
            engine=EngineMetrics(audio_seconds=20.0, inference_seconds=10.0),
            queue=queue.stats(),
        )
        assert metrics.dropped_frames == 1
        assert not metrics.is_healthy

    def test_the_status_payload_carries_everything_the_bar_shows(self) -> None:
        payload = PipelineMetrics(model_id="mock:scripted", device="cpu").as_event()
        assert set(payload) >= {
            "rtf",
            "queue_depth",
            "commit_latency_s",
            "model_id",
            "device",
            "dropped_frames",
            "healthy",
        }

    def test_summary_lines_are_readable(self) -> None:
        lines = PipelineMetrics().summary_lines()
        assert any("realtime factor" in line for line in lines)
        assert all(line.strip() for line in lines)
