"""The session manager — where the pipeline is actually wired together (BE §14).

Capture → VAD → streaming engine → transcript store → transport, with a bounded drop-oldest queue
between capture and inference so that capture never waits on transcription (constraint **C5**).

The manager is deliberately **event-loop agnostic**. It emits events by calling a plain function
from whichever thread produced them, and the transport layer is responsible for marshalling those
onto the loop. That keeps the pipeline testable without an event loop and keeps asyncio out of the
audio path entirely.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ...config import AppConfig, ConfigStore
from ...models.segment import Segment
from ...models.session import SessionMetadata, SessionStats
from ..asr import AsrLifecycle, LoadProgress, PromptBuilder
from ..asr.contract import AsrLoadError
from ..audio import LevelMeter, WavFileSource
from ..audio.sources import AudioSource, DeviceSource, SourceInfo
from ..streaming.events import CommittedSegment, EngineNotice, HypothesisUpdate
from ..streaming.guards import Severity
from ..streaming.passthrough import build_engine
from ..transcript import TranscriptStore
from ..vad import SpeechGate, build_gate
from . import degradation
from .metrics import PipelineMetrics
from .workers import DropOldestQueue, Worker

logger = logging.getLogger(__name__)

#: Emits one transport event. Called from worker threads.
EmitFn = Callable[[str, dict[str, Any]], None]

#: Roughly four level updates a second is plenty; more just drives the frontend's render loop.
LEVEL_INTERVAL_S = 0.25

#: How often health telemetry is published.
STATUS_INTERVAL_S = 1.0

#: Capture queue depth, in frames. At 32 ms a frame this is about six seconds of slack — enough to
#: absorb a slow inference pass, short enough that a sustained problem surfaces quickly.
QUEUE_CAPACITY = 200


@dataclass
class CapturedFrame:
    """One frame, with what the VAD made of it."""

    audio: np.ndarray
    speaking: bool
    pause: bool


class SessionError(RuntimeError):
    """Raised when a session operation cannot proceed."""


class SessionManager:
    """Owns the running session and everything it is made of."""

    def __init__(
        self,
        config: ConfigStore,
        emit: EmitFn,
        session_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._emit = emit
        self._session_dir = session_dir

        self._lock = threading.Lock()
        self._metadata: SessionMetadata | None = None
        self._store: TranscriptStore | None = None
        self._source: AudioSource | None = None
        self._engine: Any = None
        self._gate: SpeechGate | None = None
        self._asr = AsrLifecycle(config.resolve().asr, on_progress=self._on_load_progress)
        self._prompts: PromptBuilder | None = None

        self._queue: DropOldestQueue[CapturedFrame] = DropOldestQueue(QUEUE_CAPACITY)
        self._worker = Worker("asr-worker", self._queue, self._handle_frame)  # type: ignore[arg-type]
        self._meter = LevelMeter()
        self._status_thread: threading.Thread | None = None
        self._status_stop = threading.Event()
        self._last_level_emit = 0.0
        self._source_info: SourceInfo | None = None
        self._warned_backpressure = False
        #: Produces rolling summaries and glossary terms while a session runs. Attached by the app
        #: factory rather than constructed here: it needs a language model, and the pipeline must
        #: keep working on a machine that has none.
        self.context_worker_factory: Callable[[TranscriptStore, AppConfig], Any] | None = None
        self._context_worker: Any = None
        #: Rewrites the transcript a minute at a time for reading (D-018). Attached the same way
        #: and for the same reason: it needs a language model, and the pipeline must not.
        self.polish_worker_factory: Callable[[TranscriptStore], Any] | None = None
        self._polish_worker: Any = None

    # -- state ---------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether a session is currently recording."""
        return self._metadata is not None and self._metadata.is_running

    @property
    def store(self) -> TranscriptStore | None:
        """The current session's store, if any."""
        return self._store

    @property
    def asr(self) -> AsrLifecycle:
        """The ASR lifecycle, so routes can load and swap models."""
        return self._asr

    @property
    def session_seconds(self) -> float:
        """How far into the talk the pipeline has reached, in session-relative seconds.

        Taken from the engine — audio actually consumed — rather than from a wall clock, so it
        matches the timestamps on the transcript exactly. A file replayed at 8× would otherwise
        report a "now" eight times further along than any segment the assistant can cite.

        Falls back to the stored transcript's duration once the engine is gone, so a question asked
        after a session ends is still positioned correctly.
        """
        if self._engine is not None:
            return float(self._engine.session_seconds)
        return self._store.stats().duration_seconds if self._store is not None else 0.0

    @property
    def silence_seconds(self) -> float:
        """How long the speaker has currently been silent, from the VAD gate.

        Read by the polish worker to find a natural break to cut a chunk at. Zero while speaking,
        and permanently zero when the VAD is disabled — the polish worker's chunk ceiling is what
        covers that case.
        """
        return self._gate.silence_seconds if self._gate is not None else 0.0

    def state(self) -> dict[str, Any]:
        """A JSON-safe description of the session, for ``GET /api/session``."""
        stats = self._store.stats() if self._store else None
        return {
            "running": self.is_running,
            "session": self._metadata.as_dict() if self._metadata else None,
            "asr": self._asr.status(),
            "stats": stats.as_dict() if stats else None,
            "metrics": self.metrics().as_event(),
        }

    def metrics(self) -> PipelineMetrics:
        """Current pipeline health."""
        config = self._config.resolve()
        return PipelineMetrics(
            engine=self._engine.metrics if self._engine else PipelineMetrics().engine,
            queue=self._queue.stats(),
            model_id=self._asr.model_id,
            device=config.asr.device,
            source=self._source_info.describe() if self._source_info else "",
            detector=self._gate.detector_name if self._gate else "",
        )

    # -- lifecycle -----------------------------------------------------------------

    async def start(self, metadata: SessionMetadata | None = None) -> SessionMetadata:
        """Begin capture and transcription.

        Raises:
            SessionError: if a session is already running, or the device or model cannot be opened.
                The message names the remedy; the caller surfaces it directly.
        """
        if self.is_running:
            raise SessionError("A session is already recording. Stop it before starting another.")

        config = self._config.resolve()
        session = metadata or SessionMetadata(session_id=uuid.uuid4().hex[:12])
        session.session_prompt = config.asr.session_prompt
        session.config = config.model_dump(mode="json")

        if not self._asr.is_ready:
            await self._load_model(config)

        self._store = self._open_store(config, session)
        self._prompts = PromptBuilder(config.asr)
        self._gate = build_gate(config.vad, frame_ms=config.audio.frame_ms)
        self._engine = build_engine(
            config=config.streaming,
            transcribe=self._asr.transcribe,
            capabilities=self._asr.backend.capabilities if self._asr.backend else None,  # type: ignore[arg-type]
            model_id=self._asr.model_id,
            prompt_builder=self._prompts,
            first_segment_id=self._store.last_segment_id() + 1,
        )

        self._queue.clear()
        self._meter.reset()
        self._warned_backpressure = False
        self._worker.start()
        self._start_status_thread()

        try:
            self._source = self._open_source(config)
            self._source.start(self._on_frame, self._on_source_error)
            # Read *after* starting. A device source does not know which device it holds until it
            # has opened one, so asking first returned "No device" — and that string then went into
            # the status bar and the session's stored metadata for every live recording, while
            # capture was in fact working perfectly.
            self._source_info = self._source.info
        except Exception as exc:
            await self._teardown()
            raise SessionError(str(exc)) from exc

        self._metadata = session
        self._start_context_worker(config)
        self._start_polish_worker()
        self._emit(
            "session.started",
            {
                "session_id": session.session_id,
                "started_at": session.started_at.isoformat(),
                "config": session.config,
                "source": self._source_info.describe() if self._source_info else "",
            },
        )
        logger.info("Session %s started from %s", session.session_id, self._source_info)
        return session

    async def stop(self) -> SessionStats:
        """End the session, flushing anything pending so the last words are not lost."""
        if not self.is_running:
            raise SessionError("No session is recording.")

        assert self._metadata is not None
        session_id = self._metadata.session_id

        if self._source is not None:
            self._source.stop()

        # Stopped before the store is torn down, because its final pass summarises the tail — which
        # is where a talk's conclusions live.
        await self._stop_context_worker()

        # Drain what capture already queued before flushing: those frames are audio the speaker
        # produced, and discarding them would silently truncate the end of the talk.
        for frame in self._queue.drain():
            self._handle_frame(frame)

        if self._engine is not None:
            self._dispatch(self._engine.flush())

        # After the flush, unlike the context worker: the polish pass reads committed segments, and
        # the flush is what commits the closing sentence of the talk.
        await self._stop_polish_worker()

        stats = self._store.stats() if self._store else SessionStats(0, 0, 0.0)
        self._metadata.ended_at = datetime.now(UTC)
        if self._store is not None:
            self._store.write_metadata(self._metadata)
            self._store.mark_ended(self._metadata.ended_at)

        await self._teardown()
        self._emit("session.stopped", {"session_id": session_id, "stats": stats.as_dict()})
        logger.info("Session %s stopped: %s", session_id, stats.as_dict())
        return stats

    async def swap_model(self, config: AppConfig | None = None) -> None:
        """Change models mid-session (BE §6.6).

        The engine is flushed first, so text the old model produced is never attributed to the new
        one — which is what the per-segment ``model_id`` exists to record.
        """
        resolved = config or self._config.resolve()
        if self._engine is not None:
            self._dispatch(self._engine.flush())

        await self._asr.swap(resolved.asr)

        if self._engine is not None and self._asr.backend is not None:
            self._engine.set_model(self._asr.model_id, self._asr.backend.capabilities)
        self._emit("status", self.metrics().as_event())

    def apply_live_config(self) -> None:
        """Push changed live settings into the running pipeline."""
        config = self._config.resolve()
        if self._gate is not None:
            self._gate.update_config(config.vad)
        if self._engine is not None:
            self._engine.update_config(config.streaming)
        if self._prompts is not None:
            self._prompts.update_config(config.asr)

    async def shutdown(self) -> None:
        """Release everything. Safe to call whether or not a session is running."""
        if self.is_running:
            try:
                await self.stop()
                return
            except SessionError:
                pass
        await self._teardown()
        await self._asr.unload()

    # -- capture path --------------------------------------------------------------

    def _on_frame(self, frame: np.ndarray) -> None:
        """Called on the capture thread. Must return quickly and must never block."""
        gate = self._gate
        if gate is None:
            return

        result = gate.process(frame)
        dropped = self._queue.put(
            CapturedFrame(audio=frame, speaking=result.speaking, pause=result.pause_event)
        )
        if dropped and not self._warned_backpressure:
            self._warned_backpressure = True
            self._emit_failure(
                degradation.dropped_audio(self._queue.dropped, self._config.resolve().asr.model)
            )

        self._emit_level(frame, result.state_changed, result.speaking)

    def _emit_level(self, frame: np.ndarray, state_changed: bool, speaking: bool) -> None:
        """Publish the level meter, throttled, and the VAD state only when it changes."""
        if state_changed:
            self._emit("vad.state", {"speaking": speaking})

        now = time.monotonic()
        if now - self._last_level_emit < LEVEL_INTERVAL_S:
            return
        self._last_level_emit = now
        self._emit("audio.level", self._meter.update(frame).as_event())

    def _handle_frame(self, frame: CapturedFrame) -> None:
        """Called on the ASR worker thread. This is the expensive one, and it may block."""
        engine = self._engine
        if engine is None:
            return
        try:
            events = engine.add_audio(frame.audio, speaking=frame.speaking, pause=frame.pause)
        except AsrLoadError as exc:
            self._emit_failure(degradation.model_load_failed(self._asr.model_id, str(exc)))
            return
        self._dispatch(events)

    def _dispatch(self, events: list[Any]) -> None:
        """Persist and publish whatever the engine produced."""
        for event in events:
            if isinstance(event, CommittedSegment):
                self._persist(event.segment)
                self._emit("transcript.committed", event.segment.as_event())
            elif isinstance(event, HypothesisUpdate):
                self._emit("transcript.hypothesis", {"text": event.text, "start": event.start})
            elif isinstance(event, EngineNotice):
                self._emit("error", event.as_event()["data"])

    def _persist(self, segment: Segment) -> None:
        """Write a segment through to disk, surviving a write failure.

        A full disk must not end the session: the transcript stays in memory and is still on screen,
        and freeing space resumes saving. Stopping here would guarantee losing what might be saved.
        """
        if self._store is None:
            return
        try:
            self._store.append_segment(segment)
        except Exception as exc:  # noqa: BLE001 - any storage error, not just OSError
            logger.exception("Could not persist segment %s", segment.id)
            self._emit_failure(degradation.disk_full(type(exc).__name__))

    def _on_source_error(self, error: Exception | None) -> None:
        """The audio source stopped. ``None`` means it reached the end of its input."""
        if error is None:
            return
        name = self._source_info.name if self._source_info else "The audio device"
        self._emit_failure(degradation.device_lost(name))

    # -- status ticker -------------------------------------------------------------

    def _start_status_thread(self) -> None:
        self._status_stop.clear()
        self._status_thread = threading.Thread(
            target=self._status_loop, name="status-ticker", daemon=True
        )
        self._status_thread.start()

    def _status_loop(self) -> None:
        while not self._status_stop.wait(STATUS_INTERVAL_S):
            metrics = self.metrics()
            self._emit("status", metrics.as_event())

            # Only warn once the model has actually run: a factor of zero before the speaker starts
            # is not the system falling behind.
            if metrics.engine.inference_passes > 3 and 0.0 < metrics.real_time_factor < 1.0:
                self._emit_failure(
                    degradation.falling_behind(
                        metrics.real_time_factor, self._config.resolve().asr.model
                    )
                )

    # -- construction --------------------------------------------------------------

    def _start_context_worker(self, config: AppConfig) -> None:
        """Start rolling summarisation, if anything is configured to do it.

        Wrapped: a failure to build the worker must not prevent a session from starting. The
        assistant is a tool, the transcript is the document.
        """
        if self.context_worker_factory is None or self._store is None:
            return
        try:
            self._context_worker = self.context_worker_factory(self._store, config)
            self._context_worker.start()
        except Exception:  # noqa: BLE001 - never fatal to a recording
            logger.warning("Rolling summaries are unavailable this session", exc_info=True)
            self._context_worker = None

    async def _stop_context_worker(self) -> None:
        worker, self._context_worker = self._context_worker, None
        if worker is None:
            return
        try:
            await worker.stop()
        except Exception:  # noqa: BLE001 - a failed final summary must not fail the stop
            logger.warning("Final summary failed", exc_info=True)

    def _start_polish_worker(self) -> None:
        """Start the minute-by-minute polish pass, if anything is configured to do it.

        Wrapped for the same reason as the context worker: the transcript is the document and the
        polish is a reading aid, so a failure to build one must not stop a recording.
        """
        if self.polish_worker_factory is None or self._store is None:
            return
        try:
            self._polish_worker = self.polish_worker_factory(self._store)
            self._polish_worker.start()
        except Exception:  # noqa: BLE001 - never fatal to a recording
            logger.warning("The transcript polish pass is unavailable this session", exc_info=True)
            self._polish_worker = None

    async def _stop_polish_worker(self) -> None:
        worker, self._polish_worker = self._polish_worker, None
        if worker is None:
            return
        try:
            await worker.stop()
        except Exception:  # noqa: BLE001 - a failed final pass must not fail the stop
            logger.warning("The final polish pass failed", exc_info=True)

    def _open_store(self, config: AppConfig, session: SessionMetadata) -> TranscriptStore:
        directory = self._session_dir or Path(config.storage.session_dir)
        path = directory / f"{session.started_at.strftime('%Y%m%d-%H%M%S')}-{session.session_id}.db"
        return TranscriptStore(path, metadata=session)

    def _open_source(self, config: AppConfig) -> AudioSource:
        """Build the configured audio source."""
        if config.audio.source_type == "file":
            if not config.audio.file_path:
                raise SessionError(
                    "The file source is selected but no file is set. "
                    "Choose a WAV file, or switch to a microphone."
                )
            return WavFileSource(
                config.audio.file_path,
                frame_ms=config.audio.frame_ms,
                speed=config.audio.file_speed,
            )
        return DeviceSource(device_id=config.audio.device_id, frame_ms=config.audio.frame_ms)

    async def _load_model(self, config: AppConfig) -> None:
        """Load the ASR model, translating a failure into a remedy the user can act on."""
        try:
            await self._asr.load(config.asr)
        except AsrLoadError as exc:
            detail = str(exc)
            failure = (
                degradation.out_of_memory(config.asr.model, config.asr.device)
                if "memory" in detail.lower()
                else degradation.model_load_failed(config.asr.model, detail)
            )
            self._emit_failure(failure)
            raise SessionError(failure.message) from exc

    async def _teardown(self) -> None:
        """Stop the workers and release the session's resources."""
        # Before the store closes, and before the ordinary stop path is assumed to have run: an
        # abrupt shutdown reaches here without passing through `stop`, and a polish task still
        # ticking against a closed database would raise once a second.
        await self._stop_polish_worker()

        self._status_stop.set()
        thread = self._status_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._status_thread = None

        if self._source is not None:
            self._source.stop()
            self._source = None

        self._worker.stop()
        self._queue = DropOldestQueue(QUEUE_CAPACITY)
        self._worker = Worker("asr-worker", self._queue, self._handle_frame)  # type: ignore[arg-type]

        if self._store is not None:
            self._store.close()
            self._store = None

        self._engine = None
        self._gate = None

    # -- events --------------------------------------------------------------------

    def _emit_failure(self, failure: degradation.Failure) -> None:
        """Publish a failure, and log it at a level matching its severity."""
        payload = failure.as_event()
        if failure.severity is Severity.CRITICAL:
            logger.error("%s: %s", failure.code, failure.message)
        else:
            logger.warning("%s: %s", failure.code, failure.message)
        self._emit("error", payload)

    def _on_load_progress(self, progress: LoadProgress) -> None:
        """Forward model-load progress so the UI can name what it is waiting for."""
        self._emit("asr.progress", progress.as_dict())
