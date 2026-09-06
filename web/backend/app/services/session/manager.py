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
from ..audio.monitor import MonitorUnavailable, default_sink
from ..audio.sources import AudioSource, DeviceSource, MonitorSource, SourceInfo, probe_peak
from ..audio.tap import ApplicationTap, TapError
from ..audio.tap import playback_streams as tap_streams
from ..audio.tap import score as tap_score
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
    probe_video_duration,
)
from ..capture import detect as detect_capture
from ..recording import (
    JobRegistry,
    SinkError,
    TranscriptionJob,
    TranscriptionRunner,
    WavSink,
    layout_for,
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

#: Absolute level a monitored frame must reach to count as worth transcribing, as linear RMS.
#: −33 dBFS, chosen from measurement rather than taste: seminar speech has a 35 dB dynamic range
#: and peaks well above this, while a video's background music measured a flat 5 dB band from
#: −42.6 to −37.4 dBFS and stays below it. Speech has dynamics; ambience does not.
LOOPBACK_SPEECH_RMS = 0.022

#: How long each side of the dead-tap comparison listens. One second is ample: the question is
#: whether anything at all arrives, not what it sounds like, and both probes run before the
#: session starts — so this is time the user waits at the moment they press record.
TAP_PROBE_S = 1.0

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
    #: Which audio this run records: `system` (everything the machine plays), `application` (only
    #: the matched application, tapped additively), or `microphone`. Per-run, and it **overrides**
    #: `capture.audio_source` — the sheet in front of the user at the moment they press record is
    #: more authoritative than a setting they configured once and forgot.
    audio_source: str = "system"

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
        #: Said once per session. Repeating it every second would bury the notices that matter.
        self._warned_tap_silent = False
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
        #: The application audio tap, when window mode was asked for one. Closed in teardown —
        #: a leaked null sink shows up in the user's output picker and survives until they notice.
        self._tap: ApplicationTap | None = None
        #: Whether this run's tap narrows to the chosen window. Held so the periodic re-link
        #: applies the same rule the tap was opened with.
        self._tap_match = False
        #: Where the video landed, kept past teardown so the audio can be muxed into it.
        self._video_path = ""
        #: Seconds of sound the combined file does not carry, because the video stopped early.
        #: Non-zero makes the WAV the only complete copy of the talk, so retention stops being a
        #: setting for this run: it is kept whatever Settings → Storage says.
        self._audio_shortfall_s = 0.0
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
        #: The store of the session that has just finished, kept **open for reading** until the
        #: next one starts. See the :attr:`store` property for why.
        self._last_store: TranscriptStore | None = None

    # -- state ---------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether a session is currently recording."""
        return self._metadata is not None and self._metadata.is_running

    @property
    def store(self) -> TranscriptStore | None:
        """The session's store — the running one, or the last finished one.

        **The finished session's store stays open, and three faults came from it not doing so.**
        Everything that reads a transcript reads it through here: the assistant's
        ``store_provider``, both readers in ``routes/transcript.py``, and the stats in
        :meth:`state`. Closing it the instant a session ended left all of them with ``None``.

        What that looked like, in the order a user meets it:

        * Asking the assistant to summarise anything answered *"There is no transcript to ask
          about yet"* — while the finished transcript sat on screen. The pane's copy arrived over
          the socket during the session and outlives the store, so the interface and the server
          disagreed about whether a transcript existed.
        * Reloading the page showed an empty transcript, because the segments could not be
          re-fetched.
        * :attr:`session_seconds` fell to ``0.0``, so *"the last ten minutes"* resolved to the
          range ``[0, 0]`` and would have found nothing even had the store been present. Its own
          docstring promises the opposite; the promise was defeated here rather than unwritten.

        Held until the next session starts, which bounds the cost at exactly one SQLite connection
        and matches the point at which the previous answer stops being the one anyone wants.
        """
        return self._store or self._last_store

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

        **That fallback read ``_store`` rather than :attr:`store`, so it never fired.** Teardown
        clears ``_store``, which is exactly the moment the fallback exists for — leaving `0.0`, and
        with it a *"last ten minutes"* that resolves to the range ``[0, 0]`` and selects nothing.
        The promise above was written and then defeated one line below it.
        """
        if self._engine is not None:
            return float(self._engine.session_seconds)
        store = self.store
        return store.stats().duration_seconds if store is not None else 0.0

    @property
    def silence_seconds(self) -> float:
        """How long the speaker has currently been silent, from the VAD gate.

        Read by the polish worker to find a natural break to cut a chunk at. Zero while speaking,
        and permanently zero when the VAD is disabled — the polish worker's chunk ceiling is what
        covers that case.
        """
        return self._gate.silence_seconds if self._gate is not None else 0.0

    def state(self) -> dict[str, Any]:
        """A JSON-safe description of the session, for ``GET /api/session``.

        Reads through :attr:`store`, so a finished session still reports its totals. Reading
        ``_store`` directly is why a reload after a stop showed an empty transcript: the page asks
        here first, was told there were no stats, and had nothing to re-fetch.
        """
        store = self.store
        stats = store.stats() if store else None
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
        # The previous session's finished pass is forgotten here rather than when it ended: while
        # nothing else is running it is still the answer to "what happened to my last recording",
        # which a reload has every right to ask. Once a *new* recording starts it is only a stale
        # figure that `GET /api/session` would keep reporting alongside the live one.
        self.jobs.clear()

        config = self._config.resolve()
        session = metadata or SessionMetadata(session_id=uuid.uuid4().hex[:12])
        self._options = options if session.mode == modes.WINDOW else None
        session.session_prompt = config.asr.session_prompt
        session.config = config.model_dump(mode="json")

        if not self._asr.is_ready:
            await self._load_model(config)

        # The previous session's transcript stops being the one anyone is asking about the moment a
        # new one begins, and holding two open would leak a descriptor per recording.
        self._release_retained()
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
            self._source = self._open_source(config, session.mode)
            self._source.start(self._on_frame, self._on_source_error)
            logger.info(
                "Session %s (%s) is recording audio from: %s",
                session.session_id,
                session.mode,
                self._source.info.name,
            )
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

        # The retained transcript is held for the *next* question, and after this there will not be
        # one. Released here rather than in `_teardown`, which is precisely where it must survive.
        self._release_retained()
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

        # **A monitor source is gated on an absolute level, not an adaptive one**, and getting this
        # wrong in either direction produces a different visible fault.
        #
        # `EnergyVad` compares each frame against an *adaptive noise floor*. That is right for a
        # microphone in a room: speech spikes above a floor that settles into the gaps between
        # phrases. System output has no gaps — the floor rises to meet continuous content and
        # nothing ever clears it. Measured: **61% of frames from microphone speech pass, against 8%
        # from the system's own output**, and with three-frame hysteresis 8% scattered frames never
        # open the gate. Window recordings therefore consumed audio and committed nothing.
        #
        # Bypassing the gate entirely was tried and is worse. Whisper *invents* text on non-speech,
        # and submitting every buffer produced a transcript of disjointed fragments — "my other
        # children", "yeah it happens father" — from a gaming video's background music. A wrong
        # transcript is worse than an empty one, because it is read as real.
        #
        # An absolute threshold separates them, because a digital output has *true* silence where a
        # room only has a noise floor. Measured over one-frame RMS: seminar speech runs from −57 to
        # −21 dBFS, a 35 dB range; a video's background music sat between −42.6 and −37.4, a 5 dB
        # range. Speech has dynamics and music at conversational volume does not.
        speaking = result.speaking or self._loopback_has_content(frame)

        dropped = self._queue.put(
            CapturedFrame(audio=frame, speaking=speaking, pause=result.pause_event)
        )
        if dropped and not self._warned_backpressure:
            self._warned_backpressure = True
            self._emit_failure(
                degradation.dropped_audio(self._queue.dropped, self._config.resolve().asr.model)
            )

        self._emit_level(frame, result.state_changed, result.speaking)

    def _loopback_has_content(self, frame: np.ndarray) -> bool:
        """Whether a monitor frame is loud enough to be worth transcribing.

        Only ever true for a loopback source; a microphone keeps the adaptive gate, which earns its
        place there by stopping inference running on an empty room.
        """
        if not self._source_is_loopback:
            return False
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        return rms >= LOOPBACK_SPEECH_RMS

    @property
    def _source_is_loopback(self) -> bool:
        """Whether the open source is a monitor of the machine's own output.

        Read from the source rather than from configuration, because the file source and the
        application tap both resolve to something the configuration does not name directly.
        """
        info = self._source_info
        return info is not None and info.kind == "loopback"

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

            # **A heartbeat, not just an announcement.** `capture.state` used to be emitted exactly
            # once, when capture started — a fact broadcast into a lossy channel with no
            # reconciliation. Four ordinary events lost it permanently: a page loaded after the
            # emit, a socket reconnect, a second tab, or the emit racing the recorder into
            # existence. In every one of those the monitor pane never learned there was anything to
            # show, and nothing ever corrected it. Re-sent every second, a missed emit costs a
            # second instead of the whole recording.
            if self._recorder is not None:
                self._emit("capture.state", self.capture_state())

            # An application creates playback nodes as media starts, so the set the tap was
            # built from goes stale within seconds of pressing record.
            self._relink_tap()

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
        support = detect_capture(prefer_hardware=config.capture.encoder == "hardware")
        if not support.available:
            self._emit_failure(degradation.capture_unavailable(support.reason))
            return

        token = ""
        credentials = getattr(self, "credentials", None)
        if config.capture.reuse_consent and credentials is not None:
            token = credentials.get("capture-restore-token") or ""

        self._portal = PortalSession(cursor_mode=config.capture.cursor_mode, restore_token=token)
        stream = self._portal.open()

        # **The token is single-use**, and persisting the new one is not optional. Passing
        # `restore_token` to `SelectSources` invalidates it the moment it is used, and a fresh one
        # comes back on `Start`. An implementation that saved a token once and replayed it would
        # get a picker dialog on every recording after the first — which is precisely the symptom
        # that was diagnosed and fixed as a concurrency fault, and would have looked identical.
        if stream.restore_token and credentials is not None:
            # Stored where credentials go, never in the config file: it is a granted capability
            # and D-017 governs those.
            credentials.set("capture-restore-token", stream.restore_token)

        # Which path the portal took, so a re-prompt is diagnosable from the log rather than from
        # a user noticing. KDE restores a *window* session by matching appId and then fuzzy-matching
        # the saved title, so a browser whose tab changed will legitimately fail to match and
        # re-prompt. That is correct behaviour, not a fault to hunt.
        logger.info(
            "Portal granted a window: %s",
            "restored from a stored token"
            if token
            else "chosen fresh (no stored consent to restore)",
        )

        # One directory per recording, shared with the audio sink and named for the moment the
        # session started — which is also the transcript database's stem, and how the two are
        # joined without moving an open SQLite file.
        layout = layout_for(config.recording.recording_dir, session.started_at, session.session_id)
        layout.ensure()
        spec = build_pipeline(
            support,
            node_id=stream.node_id,
            fd=stream.fd,
            video_path=str(layout.video(support.extension)),
            preview_path=str(layout.preview),
            frame_rate=config.capture.frame_rate,
            max_height=config.capture.max_height,
            want_preview=config.capture.preview,
            # What the compositor says it is handing over. Without it the pipeline can only give
            # the scaler a range to satisfy, and a range is how a recording ended up 480x16.
            source_width=stream.width,
            source_height=stream.height,
            quality=config.capture.quality,
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
            on_health=self._on_recorder_health,
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
                # Kept for the mux, which needs it and the file's own duration to work out when
                # the video *started* — the one moment in this sequence nothing observes directly.
                self._video_stopped_at = recorder.state.stopped_at
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

        result: MuxResult = mux_audio_video(
            video, audio, video_lag_s=self._measure_video_lag(video, sink)
        )
        if not result.ok:
            logger.warning("Could not combine audio and video: %s", result.reason)
            return
        self._audio_shortfall_s = result.audio_shortfall_s
        logger.info(
            "Recording saved with audio: %s (video delayed %.3fs; silent original %s)",
            result.path,
            result.video_lag_s,
            "removed" if result.removed_source else "kept",
        )
        if result.audio_shortfall_s:
            # Said out loud rather than absorbed. The combined file is short because the picture
            # stopped, and the user is about to be told the video ended early anyway; what this
            # adds is that the sound did not, and where the whole of it still is.
            self._emit_failure(
                degradation.video_ended_early(result.audio_shortfall_s, Path(audio).name)
            )

    def _measure_video_lag(self, video: str, sink: WavSink) -> float:
        """How much later than the audio the video began, in seconds.

        **Both capture paths start on purpose at different moments**, and this is the cost of that
        decision rather than a fault in it: audio begins before the screen-cast portal is asked, so
        the first words of a talk are not lost while someone chooses a window from a dialog. The
        video then starts whenever the portal is answered and GStreamer has warmed up — a fraction
        of a second at best, and however long the dialog was on screen at worst.

        Derived rather than observed at the near end. Nothing marks the arrival of the first
        encoded frame, so the video's start is taken as *the moment it was told to stop, minus its
        own encoded duration* — two quantities that are both measurable, and whose error is a frame
        or two rather than the seconds the direct route would carry.

        Returns 0.0 whenever any input is missing, which leaves the mux doing exactly what it did
        before. An unmeasurable offset must never become a guessed one.
        """
        stopped_at = self._video_stopped_at
        audio_started_at = sink.first_write_monotonic
        if not stopped_at or not audio_started_at:
            return 0.0

        duration = probe_video_duration(video)
        if duration <= 0.0:
            logger.info("Could not read the video's duration; leaving the two tracks as captured.")
            return 0.0

        lag = (stopped_at - duration) - audio_started_at
        logger.info(
            "Capture offset: audio began %.3fs before the video (video %.2fs, audio %.2fs).",
            lag,
            duration,
            sink.duration_s,
        )
        return lag

    def _on_recorder_health(self, state: RecorderState) -> None:
        """The video started or stopped writing while the process stayed alive (D-036).

        Reported both ways round. A stall banner left standing after the capture recovered is the
        same class of fault as no banner at all — it says something untrue about a recording in
        progress — so the recovery gets its own message rather than a silent clearance.
        """
        if state.stalled:
            self._emit_failure(degradation.capture_stalled(state.stalled_seconds))
        else:
            self._emit_failure(degradation.capture_resumed())
        self._emit("capture.state", self.capture_state())

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
            # Alive and writing nothing — the state that had no name, and the reason a seminar
            # recorded fourteen minutes and then reported itself healthy for another fifteen.
            "stalled": bool(recorder and recorder.state.stalled),
            "stalled_seconds": round(recorder.state.stalled_seconds, 1) if recorder else 0.0,
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
        layout = layout_for(config.recording.recording_dir, session.started_at, session.session_id)
        try:
            return WavSink(
                layout.audio,
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
            # **Retention is not a setting when the video came up short.** The combined file is
            # then the only other copy of the sound and it does not hold all of it, so deleting
            # the WAV would destroy the part no other file contains.
            retain_audio=config.storage.retain_audio or bool(self._audio_shortfall_s),
            prompt=self._prompts.build() if self._prompts else None,
        )
        # The reference is deliberately *kept*, unlike ownership. `GET /api/transcript/...` serves
        # from it, and a reload during a half-hour pass must still show the segments already
        # committed rather than an empty page. The runner alone closes it, and tells us when.
        return started

    def _on_transcription_released(self) -> None:
        """The pass has closed the store. Reopen it for reading, and reclaim ownership.

        **This is the path `recorded` and `window` sessions take, and the fault was reported
        against one of them.** Those modes hand the store to `TranscriptionRunner`, which closes it
        itself when the batch pass finishes — so retaining it in `_teardown` cannot help here, and
        without this the assistant would go back to answering "there is no transcript to ask about
        yet" at precisely the moment the transcript becomes *complete* and most worth asking about.

        Reopened from the path rather than kept, because the object the runner closed is spent.
        `TranscriptStore(path)` opens an existing database — the same call `routes/sessions.py`
        makes for any past session — so this costs one connection and no special case.
        """
        store, self._store = self._store, None
        self._store_handed_over = False
        if store is None:
            return
        try:
            self._retain(TranscriptStore(store.path))
        except Exception:  # noqa: BLE001 - a session that has already ended must still end cleanly
            logger.debug("Could not reopen %s for reading", store.path.name, exc_info=True)

    def _retain(self, store: TranscriptStore) -> None:
        """Hold a finished session's store open for reading, replacing any already held."""
        if store is self._last_store:
            return
        self._release_retained()
        self._last_store = store
        logger.debug("Holding %s open for reading", store.path.name)

    def _release_retained(self) -> None:
        """Close the retained store, if there is one. Safe to call more than once."""
        store, self._last_store = self._last_store, None
        if store is None:
            return
        try:
            store.close()
        except Exception:  # noqa: BLE001 - a store we are done with must not break a new session
            logger.debug("The retained transcript store did not close cleanly", exc_info=True)

    def _open_store(self, config: AppConfig, session: SessionMetadata) -> TranscriptStore:
        directory = self._session_dir or Path(config.storage.session_dir)
        path = directory / f"{session.started_at.strftime('%Y%m%d-%H%M%S')}-{session.session_id}.db"
        return TranscriptStore(path, metadata=session)

    def _audio_choice(self, config: AppConfig) -> str:
        """Which audio this run should capture.

        The per-run choice wins. It is the one in front of the user at the moment they press
        record, and the whole reason the pre-flight sheet exists is that this decision changes from
        recording to recording — a talk playing in a window one minute, narration over it the next.
        """
        options = self._options
        if options is not None and options.audio_source:
            return options.audio_source
        return config.capture.audio_source

    def _open_source(self, config: AppConfig, mode: str = modes.LIVE) -> AudioSource:
        """Build the audio source this session should capture from.

        **`window` mode reads the machine's output, not the microphone.** The point of the mode is
        the window's sound; recording the person watching it was the reported fault. The portal
        carries video only (D-022) so this was always a separate capture — it was simply capturing
        the wrong thing. `capture.audio_source` moves it back to the microphone for anyone who
        wants both a window and their own commentary.

        The file source still wins outright in every mode: it is the reproducible input the whole
        pipeline is developed against, and a window session that silently ignored it would make the
        mode untestable.
        """
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
        choice = self._audio_choice(config)
        if mode == modes.WINDOW and choice == "application":
            return self._open_application_tap(config)

        if mode == modes.WINDOW and choice == "system":
            # **Also through a tap.** Recording the default sink's monitor directly is the obvious
            # implementation and it does not work here: `pw-record --target=<sink>.monitor` failed
            # to resolve against this machine's Bluetooth sink and, because an unresolved target
            # falls back to the *default source*, silently recorded the microphone instead —
            # measured at 0.97 correlation with it. Tapping every playing stream reaches the same
            # audio by the route that demonstrably works, and one mechanism is easier to keep
            # correct than two.
            try:
                return self._open_application_tap(config, match=False)
            except MonitorUnavailable as exc:
                # The microphone is not a silent substitute here — it records the wrong thing, and
                # the whole point of the mode is that it does not. Naming the remedy is better than
                # quietly capturing a voice the user did not want recorded.
                raise SessionError(str(exc)) from exc

        return DeviceSource(device_id=config.audio.device_id, frame_ms=config.audio.frame_ms)

    def _open_application_tap(self, config: AppConfig, *, match: bool = True) -> AudioSource:
        """Tap the chosen window's own audio, leaving it playing on the user's speakers.

        Used for both audio choices. `match=False` links every stream that is playing — the
        "system output" case — and `match=True` narrows to the ones that look like the window's.
        Both go through a tap because tapping is the route that works on this machine: targeting a
        sink's monitor directly silently recorded the microphone instead.

        Raises:
            MonitorUnavailable: when there is nothing to tap or the tap cannot be built. **Not
                downgraded to the microphone**, ever. Falling back to a device that records the
                person watching, in a mode whose purpose is recording the window, is the fault this
                whole path exists to fix — and it is the one that produced a transcript of the
                user's own speech over a video they were trying to record.
        """
        streams = self._tap_candidates(match)
        if not streams:
            raise MonitorUnavailable(
                "Nothing is playing any audio, so there is no window sound to record. Start the "
                "video first, or choose 'My microphone' if you meant to narrate."
            )

        tap = ApplicationTap()
        try:
            tap.open()
            # The *set*, not the best one: a browser owns a playback node per media element, and
            # linking only the top-ranked node records only whichever tab happened to be first.
            if tap.link_all(streams) == 0:
                raise TapError("no audio ports could be linked into the capture sink")
        except TapError as exc:
            tap.close()
            raise MonitorUnavailable(
                f"The window's audio could not be captured ({exc}). Choose 'My microphone' if you "
                "meant to record yourself."
            ) from exc

        self._verify_tap(tap)

        wider = self._whole_output_if_the_tap_is_dead(tap, config)
        if wider is not None:
            return wider

        self._tap = tap
        self._tap_match = match
        return MonitorSource(frame_ms=config.audio.frame_ms, tap=tap)

    def _whole_output_if_the_tap_is_dead(
        self, tap: ApplicationTap, config: AppConfig
    ) -> AudioSource | None:
        """Record the machine's whole output when the tap is built correctly and carries nothing.

        **The third question, after two that could not answer this on their own.** Listening to the
        tap alone cannot tell a dead graph from a paused video — both are bit-exact zeros, and
        refusing on that basis rejected working recordings within a day. Counting links cannot tell
        a link that carries audio from one that merely exists — which is this fault exactly: the
        sink created, the browser's ports linked, every link ``active``, every node ``running``,
        every gain 1.0, and zeros for the length of a talk.

        Asking both at once separates them, because the machine's own output is the ground truth
        neither question had:

        =============  =============  ==================================  ==================
        tap            whole output   what that means                     what happens
        =============  =============  ==================================  ==================
        silent         silent         nothing is playing yet              keep the tap
        anything       —              the tap is delivering               keep the tap
        silent         audible        the tap cannot carry this stream    record the output
        unknown        anything       the probe did not run               keep the tap
        =============  =============  ==================================  ==================

        The last row is not a formality. A probe that cannot run knows nothing, and must never be
        the thing that changes what a recording captures.

        Widening rather than refusing is the deliberate trade. What the user asked for is a
        transcript of the thing they are watching; a wider recording still contains it, and an empty
        one contains nothing. The cost — every other sound on the machine lands in the transcript —
        is real, so it is said out loud rather than absorbed silently.
        """
        sink = tap.monitor.removesuffix(".monitor")
        if probe_peak(sink, capture_sink=True, seconds=TAP_PROBE_S) != 0.0:
            # Delivering, or unmeasurable. Either way, not the fault this exists for.
            return None

        try:
            whole_output = default_sink()
        except MonitorUnavailable:
            # No output to widen to. The tap is the only route there is, so it stays.
            return None

        # **`capture_sink=True` here too, and it is load-bearing.** Widening through
        # `<name>.monitor` was this repair's own worst bug: that target does not resolve on either
        # sink on this machine, and an unresolved target falls back to the *default source* — so
        # the widened capture recorded the **microphone**, correlating with it at +1.000. It went
        # unnoticed because a microphone hears the speakers, which makes the level look right.
        # A window recording that transcribes the room is the exact fault D-028 exists to prevent,
        # and widening must not be the thing that reintroduces it.
        if (
            probe_peak(whole_output, capture_sink=True, device_sink=True, seconds=TAP_PROBE_S)
            <= 0.0
        ):
            # The machine is not playing anything, so the tap's silence is the ordinary kind —
            # someone who pressed record before pressing play. Leave it alone; it will fill.
            return None

        logger.warning(
            "The tap on %s is linked (%d ports) and delivering digital silence while the machine "
            "is playing audio. Recording the whole output instead.",
            sink,
            tap.live_links,
        )
        tap.close()
        self._tap = None
        self._emit_failure(degradation.window_audio_not_delivering())
        return MonitorSource(node=whole_output, frame_ms=config.audio.frame_ms, capture_sink=True)

    def _tap_candidates(self, match: bool) -> list:
        """The playback streams this run should tap.

        **`match` used to be documented and ignored.** Both audio choices linked every stream that
        was playing, so "record this window's audio" quietly recorded the machine's, and a second
        application making noise landed in the transcript of the first. It now narrows using the
        same `rank`/`score` heuristic the interface already presents — keeping *every* stream that
        scores rather than the single best one, because a browser owns a playback node per media
        element and picking one records whichever tab happened to be first.

        A window that matches nothing falls back to everything rather than to nothing. The match is
        a heuristic over properties PipeWire's own documentation warns are not authoritative
        (D-027), so it must not be the thing that decides a recording captures no audio at all.
        """
        streams = tap_streams()
        if not match or not streams:
            return streams

        options = self._options
        app_id = getattr(options, "window_app_id", "") or ""
        title = getattr(options, "window_title", "") or ""
        if not app_id and not title:
            return streams

        scored = [s for s in streams if tap_score(s, app_id=app_id, title=title) > 0]
        if not scored:
            logger.info("No playing stream matched the chosen window; tapping everything instead.")
            return streams
        logger.info(
            "Tapping %d of %d playing streams matched to the window", len(scored), len(streams)
        )
        return scored

    def _verify_tap(self, tap: ApplicationTap) -> None:
        """Confirm the graph is routing something into the tap, before the session commits.

        **This asked the wrong question for a day, and refused working recordings for it.** The
        first version listened to the tap and treated a run of bit-exact zeros as proof the graph
        was not delivering. That premise holds for a microphone, which always carries a noise floor,
        and is false for an application — a media player between sounds, a paused video whose stream
        is still open, or a clip with a silent lead-in all write literal zeros. Someone who pressed
        record a moment before the audio started was told the capture would be silent, and it would
        not have been.

        Whether anything is *linked* cannot be confused with whether anything is *audible*, so that
        is what is asked. It still catches the fault the check exists for — a tap nothing is routed
        into records silence for the length of a talk — and it cannot fire on a quiet moment.
        """
        if tap.live_links > 0:
            return

        tap.close()
        raise MonitorUnavailable(
            "The window's audio could not be routed into the capture — nothing is connected to it, "
            "so the recording would be silent throughout. Try starting the recording again, or "
            "choose 'My microphone' if you meant to record yourself."
        )

    def _relink_tap(self) -> None:
        """Join playback nodes that appeared after the recording started.

        **Linking once is linking too early.** `tap.py`'s own module docstring says the set has to
        be watched, because "an application creates and destroys playback nodes as the user opens
        tabs and starts media" — and the session linked once, at open, and never again. So pressing
        record and *then* pressing play produced a recording of nothing: the node carrying the video
        did not exist at the moment the tap was built. Runs from the status tick, which already
        fires once a second for the monitor pane.
        """
        tap = self._tap
        if tap is None or not tap.is_open:
            return
        try:
            added = tap.link_all(self._tap_candidates(self._tap_match))
        except TapError as exc:
            logger.debug("Could not refresh the audio tap: %s", exc)
            return
        if added:
            logger.info("Linked %d newly playing port(s) into the audio tap", added)

        # **Reported, never fatal.** If everything the tap was carrying goes away mid-recording —
        # the browser tab closed, the player quit — the rest of the session records silence, and the
        # user should hear that from the application rather than from an empty transcript
        # afterwards. Said once: repeating it every second would bury the notices that matter.
        if tap.live_links == 0 and not self._warned_tap_silent:
            self._warned_tap_silent = True
            self._emit_failure(degradation.window_audio_stopped())

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

        tap, self._tap = self._tap, None
        if tap is not None:
            tap.close()

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
            # **Retained, not closed.** This is the line the assistant's "there is no transcript to
            # ask about yet" came out of: the session ends, the handle closes, and every reader —
            # the chat orchestrator, the transcript routes, the session stats — is handed `None`
            # while the finished transcript is still on the user's screen. It stays open for
            # reading until the next session starts. See the `store` property.
            self._retain(self._store)
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
