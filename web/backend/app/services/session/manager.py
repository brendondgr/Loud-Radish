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
from ..capture import (
    MuxResult,
    PortalDeclined,
    PortalError,
    PortalSession,
    RecorderError,
    RecorderState,
    WindowRecorder,
    build_pipeline,
    mux_audio_video,
)
from ..capture import detect as detect_capture
from ..recording import (
    JobRegistry,
    SinkError,
    TranscriptionJob,
    TranscriptionRunner,
    WavSink,
)
from ..streaming.events import CommittedSegment, EngineNotice, HypothesisUpdate
from ..streaming.guards import Severity
from ..streaming.passthrough import build_engine
from ..transcript import TranscriptStore
from ..vad import SpeechGate, build_gate
from . import degradation, modes
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


@dataclass(frozen=True)
class CaptureOptions:
    """The three per-run switches a `window` session was armed with (D-020).

    A plain dataclass rather than the request schema: `services/` must not import `schemas/`, which
    is a validation boundary for HTTP and not a vocabulary for the pipeline.
    """

    live_transcription: bool = True
    post_transcription: bool = True
    video: bool = True

    @property
    def records_nothing(self) -> bool:
        return not (self.live_transcription or self.post_transcription or self.video)


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
        #: Set the instant a start is accepted and cleared when the session ends. Distinct from
        #: `is_running`, which cannot become true until the audio source is open — and the gap
        #: between the two is long enough to hold a model load, which is where concurrent starts
        #: used to slip through and open a portal dialog each.
        self._claimed = False
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
        #: `recorded` mode's file, open only while such a session runs (D-021).
        self._sink: WavSink | None = None
        #: `window` mode's video recorder and the portal session feeding it (D-022). Both are
        #: `None` in every other mode, and either may be `None` in this one — video is optional.
        self._recorder: WindowRecorder | None = None
        self._portal: PortalSession | None = None
        #: Where the video landed, kept past teardown so the audio can be muxed into it.
        self._video_path = ""
        #: The per-run options a window session was armed with.
        self._options: CaptureOptions | None = None
        #: The post-capture transcription pass. Deliberately owned here rather than by the session:
        #: it *outlives* the session, which is the whole shape of the mode.
        self.jobs = JobRegistry()
        self._runner: TranscriptionRunner | None = None
        #: True while a transcription pass owns the store. Instance state rather than an argument
        #: to `_teardown`, because `shutdown` reaches teardown by a second path that has no way to
        #: know — and did exactly that, closing a database a running pass was still writing to.
        self._store_handed_over = False

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
    def emit(self) -> EmitFn:
        """The transport's publish function, so a pass started outside a session can use it."""
        return self._emit

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
            # The post-capture pass, when one is running or has just finished. Read on page load
            # so a reload during a half-hour transcription resumes showing its progress rather than
            # an idle interface with no explanation for the missing transcript (D-021).
            "transcription": (job.as_event() if (job := self.jobs.current) else None),
            # The window capture, when one is running (D-022).
            "capture": self.capture_state() if self._recorder is not None else None,
            # Whether a recording is currently being written, and how much of one.
            "recording": (
                {
                    "duration_s": round(self._sink.duration_s, 2),
                    "bytes": self._sink.bytes_written,
                }
                if self._sink is not None
                else None
            ),
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
            suppressed=self._asr.suppressed,
        )

    # -- lifecycle -----------------------------------------------------------------

    async def start(
        self,
        metadata: SessionMetadata | None = None,
        options: CaptureOptions | None = None,
    ) -> SessionMetadata:
        """Begin capture and transcription.

        Raises:
            SessionError: if a session is already running, or the device or model cannot be opened.
                The message names the remedy; the caller surfaces it directly.
        """
        # **Claimed synchronously, before the first `await`.** `is_running` reads `self._metadata`,
        # which is not set until the audio source is up — and getting there awaits a model load that
        # can take seconds. Every start request arriving in that window passed this guard, and in
        # `window` mode each one then opened its own portal negotiation: one keystroke, five
        # consecutive "choose a window" dialogs, each cancelling the last. The claim closes the
        # window between deciding to start and having started.
        with self._lock:
            if self._claimed or self.is_running:
                raise SessionError(
                    "A session is already recording. Stop it before starting another."
                )
            self._claimed = True

        try:
            return await self._start(metadata, options)
        except BaseException:
            # Released on every failure path, including cancellation: a claim that outlives its
            # attempt makes the application permanently refuse to record.
            self._claimed = False
            raise

    async def _start(
        self,
        metadata: SessionMetadata | None,
        options: CaptureOptions | None,
    ) -> SessionMetadata:
        """The body of :meth:`start`, run with the session already claimed."""
        config = self._config.resolve()
        session = metadata or SessionMetadata(session_id=uuid.uuid4().hex[:12])
        self._options = options if session.mode == modes.WINDOW else None
        session.session_prompt = config.asr.session_prompt
        session.config = config.model_dump(mode="json")

        if not self._asr.is_ready:
            await self._load_model(config)

        self._store = self._open_store(config, session)
        self._prompts = PromptBuilder(config.asr)
        # The gate runs in every mode. In `recorded` it gates nothing — it feeds the level meter
        # and the speaking indicator, so the interface stays informative while no text is produced.
        # Skipping silence at *capture* time would produce a file whose timestamps no longer match
        # the clock, and the timestamps are what the transcript is indexed by.
        self._gate = build_gate(config.vad, frame_ms=config.audio.frame_ms)

        # The engine is what makes a session transcribe as it goes, so `recorded` mode simply does
        # not build one. That is the mode: no inference while capturing, which is what makes it
        # cheap enough to run for two hours on a laptop.
        if session.mode == modes.RECORDED:
            self._sink = self._open_sink(config, session)
            self._engine = None
        elif session.mode == modes.WINDOW:
            # Three independent switches, which is the whole substance of the mode. The audio file
            # is written whenever a second pass is wanted; the engine runs whenever live text is;
            # the recorder runs whenever video is. None of them implies another.
            options = self._options or CaptureOptions()
            if options.post_transcription:
                self._sink = self._open_sink(config, session)
            self._engine = self._build_engine(config) if options.live_transcription else None
        else:
            self._engine = self._build_engine(config)

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

        # After the audio source is up, deliberately. The portal shows a dialog and waits for a
        # human; doing that first would leave the microphone unopened for as long as they take,
        # and the first words of a talk are said while someone is still choosing a window.
        if session.mode == modes.WINDOW and (self._options or CaptureOptions()).video:
            try:
                self._start_window_capture(config, session)
            except PortalDeclined as exc:
                # They said no. Starting an audio-only recording they did not ask for would be
                # reading a refusal as a yes.
                await self._teardown()
                self._metadata = None
                raise SessionError(
                    "Screen sharing was cancelled, so nothing was recorded."
                ) from exc
            except (PortalError, RecorderError) as exc:
                # Everything else costs the video and keeps the talk.
                logger.warning("Video capture could not start: %s", exc)
                self._emit_failure(degradation.capture_failed(str(exc)))

        self._start_context_worker(config)
        self._start_polish_worker()
        self._emit(
            "session.started",
            {
                "session_id": session.session_id,
                "started_at": session.started_at.isoformat(),
                "mode": session.mode,
                "config": session.config,
                "source": self._source_info.describe() if self._source_info else "",
            },
        )
        logger.info(
            "Session %s started in %s mode from %s",
            session.session_id,
            session.mode,
            self._source_info,
        )
        return session

    async def stop(self) -> SessionStats:
        """End the session, flushing anything pending so the last words are not lost."""
        if not self.is_running:
            raise SessionError("No session is recording.")

        assert self._metadata is not None
        session_id = self._metadata.session_id

        if self._source is not None:
            self._source.stop()

        # **Said as soon as it is true.** Releasing the device is instant; everything after it —
        # finalising a video container, remuxing, a transcription pass — can take tens of seconds,
        # and until this event existed the interface sat on "Stopping…" for all of it with no way
        # to tell a slow finalise from a hang. The microphone or monitor is already closed by the
        # time this is emitted, which is the fact the user actually wants confirmed.
        self._emit(
            "session.capture_ended",
            {
                "session_id": session_id,
                "finalising": bool(self._recorder is not None or self._sink is not None),
            },
        )

        # Before the audio sink closes: finalising the container takes a few seconds, and the
        # timestamps line up better if the audio is still being written while it happens.
        self._stop_window_capture()

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
        # Both files are closed by now — the recorder finalised its container above, and the sink
        # is about to. Muxing needs both complete, which is why it is here and not in either.
        self._mux_if_wanted()
        if self._store is not None:
            self._store.write_metadata(self._metadata)

        # In `recorded` mode the session's work is only now beginning. The pass is handed the store
        # — which is why `mark_ended` is not called here for it: the recording is not finished until
        # its transcript exists.
        self._store_handed_over = self._start_transcription(self._metadata)
        if not self._store_handed_over and self._store is not None:
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
            except SessionError:
                pass
        else:
            await self._teardown()

        # After the stop, not instead of it: stopping is what *starts* the pass in recorded mode.
        # It is asked to finish at the next window rather than waited out — a server taking half an
        # hour to exit is one nobody will let start automatically, and every segment produced so
        # far is already committed.
        if self._runner is not None:
            self._runner.stop()
            self._runner = None
        await self._asr.unload()

    # -- capture path --------------------------------------------------------------

    def _on_frame(self, frame: np.ndarray) -> None:
        """Called on the capture thread. Must return quickly and must never block."""
        gate = self._gate
        if gate is None:
            return

        result = gate.process(frame)

        # Written here, on the capture thread, rather than through the queue. The queue is
        # drop-oldest by design — it protects inference latency by discarding audio — and audio
        # discarded from a *recording* is a hole in the only copy of the talk. A buffered write of
        # a kilobyte is several orders of magnitude cheaper than the inference pass the queue
        # exists to decouple from, so this does not reintroduce the blocking it guards against.
        sink = self._sink
        if sink is not None and sink.write(frame) and not sink.is_closed:
            self._emit_failure(degradation.recording_capped(sink.duration_s / 60.0))

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

            # In `recorded` mode this is the *only* sign the application is doing anything: no
            # transcript is being produced, so a figure that climbs is what distinguishes recording
            # from having silently stopped (D-021).
            sink = self._sink
            if sink is not None and not sink.is_closed:
                self._emit(
                    "recording.progress",
                    {"duration_s": round(sink.duration_s, 2), "bytes": sink.bytes_written},
                )

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

    def _build_engine(self, config: AppConfig) -> Any:
        """The streaming engine, which is what makes a session transcribe as it goes."""
        assert self._store is not None
        return build_engine(
            config=config.streaming,
            transcribe=self._asr.transcribe,
            capabilities=self._asr.backend.capabilities if self._asr.backend else None,  # type: ignore[arg-type]
            model_id=self._asr.model_id,
            prompt_builder=self._prompts,
            first_segment_id=self._store.last_segment_id() + 1,
        )

    def _start_window_capture(self, config: AppConfig, session: SessionMetadata) -> None:
        """Ask for a window and start recording it. Only called when video was requested.

        **A failure here does not fail the session.** The audio is already capturing and its
        transcript is the part that cannot be recreated; losing the video is a disappointment,
        losing the talk is not recoverable. The one exception is the user declining, which ends the
        whole run — they said no, and starting an audio recording they did not ask for would be
        taking the refusal as a yes.
        """
        support = detect_capture()
        if not support.available:
            self._emit_failure(degradation.capture_unavailable(support.reason))
            return

        token = ""
        credentials = getattr(self, "credentials", None)
        if config.capture.reuse_consent and credentials is not None:
            token = credentials.get("capture-restore-token") or ""

        self._portal = PortalSession(cursor_mode=config.capture.cursor_mode, restore_token=token)
        stream = self._portal.open()

        if stream.restore_token and credentials is not None:
            # Stored where credentials go, never in the config file: it is a granted capability
            # and D-017 governs those.
            credentials.set("capture-restore-token", stream.restore_token)

        directory = Path(config.recording.recording_dir)
        stamp = session.started_at.strftime("%Y%m%d-%H%M%S")
        base = f"{stamp}-{session.session_id}"
        spec = build_pipeline(
            support,
            node_id=stream.node_id,
            fd=stream.fd,
            video_path=str(directory / f"{base}.{support.extension}"),
            preview_path=str(directory / f"{base}-preview.jpg"),
            frame_rate=config.capture.frame_rate,
            max_height=config.capture.max_height,
            want_preview=config.capture.preview,
            # What the compositor says it is handing over. Without it the pipeline can only give
            # the scaler a range to satisfy, and a range is how a recording ended up 480x16.
            source_width=stream.width,
            source_height=stream.height,
        )
        # The source size is worth a line of its own: when a capture records the wrong thing, this
        # is what says whether the compositor handed over the wrong node or the pipeline mangled a
        # correct one, and the two have nothing in common as faults.
        logger.info(
            "Recording PipeWire node %s, source %sx%s: %s",
            stream.node_id,
            stream.width,
            stream.height,
            spec.command,
        )

        self._recorder = WindowRecorder(
            spec,
            portal_fd=stream.fd,
            on_stopped=self._on_recorder_stopped,
            log_dir=Path("logs"),
        )
        self._recorder.start()
        self._emit("capture.state", self.capture_state())

    def _stop_window_capture(self) -> None:
        """Finalise the video and release the portal. Safe in any mode and at any point."""
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            try:
                recorder.stop()
                self._video_path = recorder.state.video_path
            except Exception:  # noqa: BLE001 - a failed teardown must not fail the stop
                logger.exception("The video recorder did not stop cleanly")

        portal, self._portal = self._portal, None
        if portal is not None:
            # Closing the portal session is what makes the compositor's sharing indicator go away.
            # Leaving it open shows the user they are still sharing a window when they are not.
            portal.close()

    def _mux_if_wanted(self) -> None:
        """Combine the video with the session's audio, when there is both and it was asked for.

        Never fatal. Failing costs one convenience — two files instead of one — and both are
        playable on their own, so a mux must not be able to fail a recording.
        """
        video, self._video_path = self._video_path, ""
        sink = self._sink
        if not video or sink is None or sink.samples == 0:
            return
        if not self._config.resolve().capture.mux_audio:
            return

        # The sink has to be closed before ffmpeg reads it, and closing it here rather than leaving
        # it to `_start_transcription` is safe: `close()` is idempotent.
        try:
            audio = sink.close()
        except SinkError:
            return

        result: MuxResult = mux_audio_video(video, audio, keep_sources=True)
        if not result.ok:
            logger.warning("Could not combine audio and video: %s", result.reason)
            return
        logger.info("Recording saved with audio: %s", result.path)

    def _on_recorder_stopped(self, state: RecorderState) -> None:
        """The video ended without being asked to. Usually the window was closed."""
        if state.window_closed:
            self._emit_failure(degradation.capture_window_closed())
        elif state.failed:
            self._emit_failure(degradation.capture_failed(state.error))
        self._emit("capture.state", self.capture_state())

    def capture_state(self) -> dict[str, Any]:
        """What the monitor pane draws. Empty outside `window` mode."""
        recorder = self._recorder
        options = self._options
        return {
            "recording": bool(recorder and recorder.is_running),
            "window_closed": bool(recorder and recorder.state.window_closed),
            "failed": bool(recorder and recorder.state.failed),
            "error": recorder.state.error if recorder else "",
            "video_path": recorder.state.video_path if recorder else "",
            "bytes": recorder.state.bytes_written if recorder else 0,
            "duration_s": round(recorder.state.duration_s, 1) if recorder else 0.0,
            "preview": bool(recorder and recorder.state.preview_path),
            "options": {
                "live_transcription": bool(options and options.live_transcription),
                "post_transcription": bool(options and options.post_transcription),
                "video": bool(options and options.video),
            }
            if options
            else None,
        }

    def _open_sink(self, config: AppConfig, session: SessionMetadata) -> WavSink:
        """Open the file this session records into.

        A failure here is fatal to the session on purpose, unlike almost everything else in this
        class: `recorded` mode with no file is a mode that records nothing and then reports
        success, which is the worst possible outcome for a talk that will not happen twice.
        """
        directory = Path(config.recording.recording_dir)
        stamp = session.started_at.strftime("%Y%m%d-%H%M%S")
        try:
            return WavSink(
                directory / f"{stamp}-{session.session_id}.wav",
                max_minutes=config.recording.max_minutes,
            )
        except SinkError as exc:
            raise SessionError(str(exc)) from exc

    def _start_transcription(self, session: SessionMetadata) -> bool:
        """Begin the post-capture pass, if this session produced a recording.

        Returns whether the store was handed to the runner, which then owns closing it.
        """
        sink, self._sink = self._sink, None
        if sink is None or self._store is None:
            return False

        try:
            path = sink.close()
        except SinkError as exc:
            logger.error("Could not finalise the recording: %s", exc)
            self._emit_failure(degradation.disk_full(str(exc)))
            return False

        if sink.samples == 0:
            # A session that captured nothing has nothing to transcribe, and an empty file left on
            # disk is only ever confusing.
            path.unlink(missing_ok=True)
            logger.info("Session %s captured no audio; nothing to transcribe.", session.session_id)
            return False

        config = self._config.resolve()
        job = TranscriptionJob(
            session_id=session.session_id,
            source_path=str(path),
            total_seconds=sink.duration_s,
        )
        # A session that also transcribed live already holds revision 0, so the post-capture pass
        # writes revision 1 and both are kept (D-022). A `recorded` session has no live pass, so
        # its only transcript is revision 0 and a switch would have nothing to switch between.
        revision = 1 if self._engine is not None else 0

        self._runner = TranscriptionRunner(
            registry=self.jobs,
            emit=self._emit,
            transcribe=self._asr.transcribe,
            window_s=config.recording.batch_window_s,
            overlap_s=config.recording.batch_overlap_s,
            max_segment_s=config.streaming.max_segment_s,
            revision=revision,
            on_released=self._on_transcription_released,
        )
        started = self._runner.start(
            job=job,
            store=self._store,
            retain_audio=config.storage.retain_audio,
            prompt=self._prompts.build() if self._prompts else None,
        )
        # The reference is deliberately *kept*, unlike ownership. `GET /api/transcript/...` serves
        # from it, and a reload during a half-hour pass must still show the segments already
        # committed rather than an empty page. The runner alone closes it, and tells us when.
        return started

    def _on_transcription_released(self) -> None:
        """The pass has closed the store. Stop reading from it, and reclaim ownership."""
        self._store = None
        self._store_handed_over = False

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
        """Stop the workers and release the session's resources.

        The store is left alone while a transcription pass owns it — closing it here would pull a
        SQLite connection out from under a thread mid-write, which is exactly what happened when
        this was a parameter instead of instance state: `shutdown` reaches teardown by a path that
        had no way to know a pass was running.
        """
        # Released here rather than in `stop`, because every way a session ends reaches teardown —
        # a clean stop, a declined portal, an abrupt shutdown — and a claim that survives one of
        # them makes the application refuse to record for the rest of its life.
        self._claimed = False

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

        if self._store is not None and not self._store_handed_over:
            self._store.close()
            self._store = None

        # An abrupt teardown that never reached `stop` still has a file open. Discarded rather than
        # kept: nothing has been transcribed from it and nothing knows it exists.
        if self._sink is not None:
            self._sink.discard()
            self._sink = None

        # Same for the video, and for the portal session behind it — an abrupt shutdown that left
        # the portal open would leave the compositor telling the user they are still sharing.
        self._stop_window_capture()
        self._options = None

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
