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
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...config import AppConfig, ConfigStore
from ...models.session import SessionMetadata, SessionStats
from ..asr import AsrLifecycle, LoadProgress, PromptBuilder
from ..asr.contract import AsrLoadError
from ..audio import LevelMeter
from ..audio.sources import AudioSource, SourceInfo
from ..audio.tap import ApplicationTap
from ..capture import (
    CaptureSupport,
    PortalDeclined,
    PortalError,
    PortalSession,
    RecorderError,
    WindowRecorder,
)
from ..recording import (
    JobRegistry,
    RecordingLayout,
    SinkError,
    TranscriptionRunner,
    WavSink,
    layout_for,
)
from ..streaming.guards import Severity
from ..transcript import TranscriptStore
from ..vad import SpeechGate, build_gate
from . import degradation, modes
from .background import BackgroundWorkMixin
from .frames import FramePathMixin
from .metrics import PipelineMetrics
from .passes import TranscriptionPassMixin
from .shapes import (
    QUEUE_CAPACITY,
    CapturedFrame,
    CaptureOptions,
    EmitFn,
    SessionError,
)
from .sources import AudioSourceMixin
from .window_capture import WindowCaptureMixin
from .workers import DropOldestQueue, Worker

logger = logging.getLogger(__name__)


class SessionManager(
    FramePathMixin,
    BackgroundWorkMixin,
    WindowCaptureMixin,
    TranscriptionPassMixin,
    AudioSourceMixin,
):
    """Owns the running session and everything it is made of.

    **The five bases are this class, split across files, not five collaborators.** ``manager.py``
    had reached 1782 lines against an 800-line cap, and every feature in
    ``docs/plans/tray-restart-clutter-and-interruptible-work.md`` edits it. The methods moved out
    read and write this object's attributes exactly as they did when they were written here; the
    move changed no behaviour, which is what the suite passing untouched demonstrates.

    Mixins were chosen over real collaborator objects deliberately and with a known cost. Real
    objects would need their own state, their own lifetimes and their own teardown ordering — a
    refactor with genuine risk, in the file where a concurrency mistake once produced "Cannot
    operate on a closed database". Splitting by file first is the cheap half of that change and
    leaves the expensive half available. What it does not do is reduce the coupling: these methods
    still know everything about this object, and the seam is a filename rather than an interface.
    """

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
        #: Every piece of this session's video, in capture order: the path, and the monotonic
        #: moment that piece was told to stop. A capture that never broke has one. The starts are
        #: derived from these at finalisation, the same way the mux's offset always has been.
        self._capture_segments: list[tuple[str, float]] = []
        #: How many times this session's capture has been reopened after dying. Bounded, because a
        #: portal that will never come back should produce one message and not a restart loop.
        self._capture_resumes = 0
        #: Monotonic time of the last resume attempt, successful or not.
        self._last_resume_at = 0.0
        #: What a resumed capture needs to rebuild the identical pipeline. Held from the first
        #: start, because `detect_capture` is not free and its answer cannot change mid-session.
        self._capture_support: CaptureSupport | None = None
        self._capture_layout: RecordingLayout | None = None
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
        # **Pause is a flag on the capture thread, not a stopped device.** Releasing the microphone
        # and reopening it would give the resumed session a different stream, a different clock
        # origin and — on a monitor source — possibly a different sink; and the portal would ask
        # again. Holding the device and dropping its frames costs an idle callback every 32 ms and
        # keeps everything else identical (D-044).
        self._paused = False
        #: Set when a session was ended by `cancel()` rather than by `stop()`. Read at teardown to
        #: skip the post-capture pass — the only difference between the two.
        self._cancelled = False

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
    def is_paused(self) -> bool:
        """Whether a running session is being held. False when nothing is running."""
        return self._paused and self.is_running

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
            # Separate from `running`, because a paused session *is* running: it holds its device,
            # its file and its store. A client that only read `running` would show a recording that
            # is not advancing as one that is, which is the misreading that costs a talk.
            "paused": self.is_paused,
            # How much audio this session has actually consumed, which is *not* wall-clock elapsed
            # once it has been paused (D-044). The page's clock is derived from the start time, so
            # on a reload it has no way to know how long the session was held; this is the figure it
            # reconciles against, and it is the same one every transcript timestamp comes from.
            "recorded_seconds": round(self.session_seconds, 2),
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
            self._paused = False
            self._cancelled = False
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
        self._gate = build_gate(
            config.vad,
            frame_ms=config.audio.frame_ms,
            # Announced, not merely logged: this application ran the energy detector while
            # reporting Silero for months because nobody reads a warning (D-052).
            on_fallback=lambda why: self._emit_failure(degradation.voice_detector_fell_back(why)),
        )

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
                "key": self._session_key(),
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

    def pause(self) -> None:
        """Hold the capture. The recording stops growing and the clock stops with it.

        **Pause removes time from the recording rather than recording silence (D-044).** Frames are
        dropped at `_on_frame`, so the WAV stops growing, the engine consumes nothing and its
        `session_seconds` — which is where every transcript timestamp comes from — stops advancing.
        A ten-minute talk held for five minutes produces ten minutes of audio whose last timestamp
        is 10:00.

        The alternative, writing silence, was rejected twice over: it makes the transcript claim
        time in which nothing was said, and it makes pausing cost exactly as much disk and exactly
        as much inference as not pausing, which is not what the word means. What is given up is that
        timestamps no longer correspond to time of day once a session has been paused — and nothing
        reads them that way. The polish pass, the assistant's citations and the exported page's
        video sync are all recording-relative.

        The device stays open. Closing and reopening it would change the stream, the clock origin
        and possibly the sink, and in `window` mode would ask the portal again.

        Raises:
            SessionError: if nothing is recording.
        """
        if not self.is_running:
            raise SessionError("No session is recording.")
        if self._paused:
            return

        self._paused = True
        self._pause_window_capture()
        self._emit(
            "session.paused",
            {
                "session_id": self._metadata.session_id if self._metadata else "",
                "at_seconds": round(self.session_seconds, 2),
            },
        )
        logger.info("Session held at %.1f s", self.session_seconds)

    def resume(self) -> None:
        """Continue a held capture, in the same session, file and store.

        Raises:
            SessionError: if nothing is recording.
        """
        if not self.is_running:
            raise SessionError("No session is recording.")
        if not self._paused:
            return

        self._paused = False
        self._resume_window_capture_after_pause()
        self._emit(
            "session.resumed",
            {
                "session_id": self._metadata.session_id if self._metadata else "",
                "at_seconds": round(self.session_seconds, 2),
            },
        )
        logger.info("Session resumed at %.1f s", self.session_seconds)

    async def cancel(self) -> SessionStats:
        """End the session and transcribe nothing. Keeps every artefact it produced.

        **Cancel keeps everything (D-044).** It stops capture, skips the post-capture pass, and
        leaves the audio, the video and whatever segments were already committed exactly where they
        are. Deleting is a separate and explicit act from the Recordings page.

        A control that discards a recording is one that will eventually discard the wrong one, and
        the whole disposition of this application is against that — D-027 refused to move a stream
        the user was listening to, and D-036 made `mux.py` delete a source only once the output
        demonstrably contains it. The difference between stopping and cancelling is therefore
        exactly one thing: whether the transcription pass runs.
        """
        if not self.is_running:
            raise SessionError("No session is recording.")

        self._cancelled = True
        self._paused = False
        stats = await self.stop()
        self._emit(
            "session.cancelled",
            {"session_id": stats.session_id if hasattr(stats, "session_id") else "", "kept": True},
        )
        return stats

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
        # **The key, not only the id.** Everything about a finished recording is addressed by the
        # transcript database's stem — the recordings listing, the exports, the media route — and a
        # client that had only the session id would have to search a listing to find the recording
        # it had just made. `session.started` carries it for the same reason.
        self._emit(
            "session.stopped",
            {
                "session_id": session_id,
                "key": self._session_key(),
                "stats": stats.as_dict(),
                # **Whether there is anything to export.** The interface opens its export window
                # when a recording finishes, and it used to open for *every* session — including a
                # plain live transcription, which writes no media at all. The result was a dialog
                # offering five video qualities over the words "This recording has no video",
                # after every dictation and every talk. Reported.
                "has_media": self._has_media(),
            },
        )
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
