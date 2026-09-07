"""Recording a window's picture: the portal, the recorder, the resume, and the join (D-022, D-036).

Split out of ``manager.py`` when that file passed twice its 800-line cap. **Mixin methods on**
:class:`~.manager.SessionManager` — see the note in ``frames.py`` for why that shape was chosen.

This is the half of a window session that negotiates with the compositor for pixels. The other half
— what the session does with sound — is in ``sources.py``, and the two are deliberately separate
because they fail for unrelated reasons and are diagnosed by unrelated means.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ...config import AppConfig
from ...models.session import SessionMetadata
from ..capture import (
    CaptureSegment,
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
    stitch_segments,
)
from ..capture import detect as detect_capture
from ..recording import SinkError, WavSink, layout_for
from ..recording.layout import key_for as layout_key_for
from . import degradation
from .shapes import MAX_CAPTURE_RESUMES, RESUME_BACKOFF_S

logger = logging.getLogger(__name__)


class WindowCaptureMixin:
    """See the module docstring: these are methods of ``SessionManager``."""

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
        self._capture_support = support
        self._capture_segments = []
        self._capture_resumes = 0

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
        self._capture_layout = layout
        spec = build_pipeline(
            support,
            node_id=stream.node_id,
            fd=stream.fd,
            video_path=str(layout.video_segment(1, support.extension)),
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

    # -- resuming a capture that died mid-session (D-036) ------------------------------

    def _resume_window_capture(self) -> bool:
        """Reopen the portal and start recording again, into the next segment.

        **The user's own request, and the machinery was already all there.** The restore token is
        persisted on every start precisely so a later recording skips the picker; using it to
        reopen *within* a session is the same call. What was missing is anyone making it: a video
        that ended mid-talk simply stayed ended, which cost a seminar its last thirty-nine minutes.

        Returns whether a new recorder is running. Every refusal is quiet and bounded — a portal
        that will never come back should produce one message and not a restart loop.
        """
        config = self._config.resolve()
        support = self._capture_support
        layout = self._capture_layout
        session = self._metadata
        if support is None or layout is None or session is None or not self.is_running:
            return False

        now = time.monotonic()
        if self._capture_resumes >= MAX_CAPTURE_RESUMES:
            logger.info(
                "Not reopening the capture again: %d attempts is the limit.",
                self._capture_resumes,
            )
            return False
        if self._last_resume_at and now - self._last_resume_at < RESUME_BACKOFF_S:
            return False

        self._capture_resumes += 1
        self._last_resume_at = now

        # **Silently, or not at all.** KDE restores a window session by fuzzy-matching the saved
        # title, so a token can legitimately fail to match — and the compositor's answer to that is
        # to put its picker on screen, over the talk being recorded, un-asked-for. A capture that
        # ends with a banner is a disappointment; a dialog thrown across a live seminar is worse.
        credentials = getattr(self, "credentials", None)
        token = ""
        if credentials is not None:
            token = credentials.get("capture-restore-token") or ""
        if not token:
            logger.info("Not reopening the capture: no stored consent to restore it with.")
            return False

        self._release_portal()
        try:
            self._portal = PortalSession(
                cursor_mode=config.capture.cursor_mode, restore_token=token
            )
            stream = self._portal.open()
        except (PortalDeclined, PortalError) as exc:
            logger.info("Could not reopen the capture: %s", exc)
            self._release_portal()
            return False

        if stream.restore_token and credentials is not None:
            credentials.set("capture-restore-token", stream.restore_token)

        index = len(self._capture_segments) + 1
        video_path = layout.video_segment(index, support.extension)
        spec = build_pipeline(
            support,
            node_id=stream.node_id,
            fd=stream.fd,
            video_path=str(video_path),
            preview_path=str(layout.preview),
            frame_rate=config.capture.frame_rate,
            max_height=config.capture.max_height,
            want_preview=config.capture.preview,
            source_width=stream.width,
            source_height=stream.height,
            quality=config.capture.quality,
        )
        try:
            self._recorder = WindowRecorder(
                spec,
                portal_fd=stream.fd,
                on_stopped=self._on_recorder_stopped,
                on_health=self._on_recorder_health,
                log_dir=Path("logs"),
                log_name=f"capture-{index:03d}.log",
            )
            self._recorder.start()
        except RecorderError as exc:
            logger.info("Could not restart the video recorder: %s", exc)
            self._recorder = None
            self._release_portal()
            return False

        logger.info("Video capture resumed into %s (attempt %d).", video_path.name, index)
        self._emit_failure(degradation.capture_resumed())
        self._emit("capture.state", self.capture_state())
        return True

    def _retire_recorder(self, recorder: WindowRecorder) -> None:
        """Record where a finished piece landed and when it stopped.

        Those two facts are what put it on the session's timeline: a segment's first frame is *the
        moment it was told to stop, minus its own encoded duration* — the same derivation the mux's
        offset has always used, because the near end is not observable either way.
        """
        path = recorder.state.video_path
        if not path:
            return
        stopped_at = recorder.state.stopped_at or time.monotonic()
        if any(existing == path for existing, _ in self._capture_segments):
            return
        self._capture_segments.append((path, stopped_at))

    def _release_portal(self) -> None:
        portal, self._portal = self._portal, None
        if portal is None:
            return
        try:
            portal.close()
        except Exception:  # noqa: BLE001 - releasing consent must not fail a recording
            logger.debug("Could not close the portal session", exc_info=True)

    def _pause_window_capture(self) -> None:
        """Stop the video while the session is held, so it cannot drift from the audio (D-044).

        **The audio clock is the only clock**, and a pause stops it. If the encoder kept running
        through a five-minute hold, the picture would be five minutes longer than the sound and
        every frame after the pause would sit that far ahead of its own transcript line — the exact
        desynchronisation D-036's stitching exists to prevent.

        So the recorder is finalised into the segment it was writing, and a resume starts the next
        one. Nothing else is needed: `stitch` places each piece by the session second its first
        frame was captured at, and because the audio clock did not advance during the hold, the next
        piece's start lands where the previous one ended. The gap it computes is therefore below
        `MIN_GAP_S` and no filler is inserted — the same code that *holds* a frame across an
        unintended outage *closes* an intended one, because the difference is entirely in the clock.
        """
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        try:
            recorder.stop()
            self._retire_recorder(recorder)
        except Exception:  # noqa: BLE001 - a pause must never end the session
            logger.exception("The video recorder did not pause cleanly")

    def _resume_window_capture_after_pause(self) -> bool:
        """Start the next video segment after a hold.

        Uses the same reopen as a stall does, and **does not spend its allowance**: the five-attempt
        cap in `_resume_window_capture` exists to stop a portal that will never come back from being
        asked forever, and a user pressing resume is not that. A hold that could only be lifted five
        times would be a strange thing to explain.
        """
        if self._recorder is not None or self._capture_support is None:
            return False
        # The backoff is lifted too, and for the same reason: it protects against a capture that
        # dies the instant it starts, not against someone pressing resume four seconds after pause.
        before, self._last_resume_at = self._capture_resumes, 0.0
        try:
            resumed = self._resume_window_capture()
        finally:
            self._capture_resumes = before
        return resumed

    def _stop_window_capture(self) -> None:
        """Finalise the video and release the portal. Safe in any mode and at any point."""
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            try:
                recorder.stop()
                self._retire_recorder(recorder)
            except Exception:  # noqa: BLE001 - a failed teardown must not fail the stop
                logger.exception("The video recorder did not stop cleanly")

        # Closing the portal session is what makes the compositor's sharing indicator go away.
        # Leaving it open shows the user they are still sharing a window when they are not.
        self._release_portal()
        self._join_capture_segments()

    def _join_capture_segments(self) -> None:
        """Put a capture that had to be restarted back onto one timeline (D-036).

        Each piece's first frame is derived the same way the mux's offset is — the moment it was
        told to stop, minus its own encoded duration — because that is the only end of a GStreamer
        pipeline this process can observe. The gaps between the pieces are then known rather than
        guessed, and `stitch` holds a frame across each one so nothing after a gap slides out of
        sync with the transcript.

        **A failure keeps the pieces and takes the first one.** They all play; combining them
        wrongly would be worse than not combining them, and the first is the one the mux and the
        alignment measurement were already built around.
        """
        segments = self._capture_segments
        if not segments:
            self._video_path = ""
            self._video_stopped_at = 0.0
            return

        # Kept for the mux, which needs it and the file's own duration to work out when the video
        # *started* — the one moment in this sequence nothing observes directly. It is the *first*
        # piece's, not the last: a stitched file opens on the first piece's first frame.
        first_path, first_stopped_at = segments[0]
        self._video_path = first_path
        self._video_stopped_at = first_stopped_at

        if len(segments) == 1:
            return

        pieces: list[CaptureSegment] = []
        for path, stopped_at in segments:
            duration = probe_video_duration(path)
            if duration <= 0.0:
                logger.warning("Cannot place %s on the timeline; keeping the pieces apart.", path)
                return
            pieces.append(CaptureSegment(Path(path), stopped_at - duration))

        output = Path(first_path)
        joined = output.with_name(f"{output.stem}.joined{output.suffix}")
        result = stitch_segments(pieces, joined)
        if not result.ok:
            logger.warning("Could not join the recorded pieces: %s", result.reason)
            self._emit_failure(degradation.capture_pieces_kept(len(segments)))
            return

        # The joined file takes the plain name, so nothing downstream — the export, the media
        # indicators, the recordings listing — has to learn that segments exist.
        for path, _stopped_at in segments:
            Path(path).unlink(missing_ok=True)
        joined.replace(output)
        self._video_path = str(output)
        self._emit_failure(degradation.capture_pieces_joined(len(segments), result.filled_s))

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
        if not state.stalled:
            self._emit_failure(degradation.capture_resumed())
            self._emit("capture.state", self.capture_state())
            return

        self._emit_failure(degradation.capture_stalled(state.stalled_seconds))
        self._emit("capture.state", self.capture_state())

        # **A stall is acted on, not only reported.** The stream that produced this recording's
        # frames stopped at 14 m 43 s and the process stayed alive until 29 m 25 s: waiting for it
        # to end on its own cost fifteen minutes of picture that a reopened portal would have had.
        # Ending it here is what turns the detection into a recovery.
        recorder = self._recorder
        if recorder is None:
            return
        threading.Thread(
            target=self._restart_stalled_capture,
            args=(recorder,),
            name="capture-restart",
            daemon=True,
        ).start()

    def _restart_stalled_capture(self, recorder: WindowRecorder) -> None:
        """End a stalled capture and open its successor. Runs off the watcher's own thread.

        `stop()` joins the watcher, and the watcher is what called us — so doing this inline would
        have the supervisor waiting for itself.
        """
        if self._recorder is not recorder:
            return
        try:
            recorder.stop()
        except Exception:  # noqa: BLE001 - a stalled recorder that will not stop must not raise here
            logger.exception("A stalled video recorder did not stop cleanly")
        self._retire_recorder(recorder)
        self._recorder = None
        if not self._resume_window_capture():
            self._emit_failure(degradation.capture_failed("the window stopped sending pictures."))
            self._emit("capture.state", self.capture_state())

    def _on_recorder_stopped(self, state: RecorderState) -> None:
        """The video ended without being asked to. Usually the window was closed.

        **And now it is a reason to try again.** "The window was closed" and "the stream went away"
        are indistinguishable from here — a Zoom call that drops its connection and rebuilds its
        surface produces exactly the clean end that a person closing a window does — so both are
        treated as resumable while the session is still running. If the portal restores, the talk
        keeps being recorded; if it does not, the message is the one that was always shown.
        """
        recorder = self._recorder
        if recorder is not None and recorder.state is state:
            self._retire_recorder(recorder)
            self._recorder = None
            if self._resume_window_capture():
                return

        if state.window_closed:
            self._emit_failure(degradation.capture_window_closed())
        elif state.failed:
            self._emit_failure(degradation.capture_failed(state.error))
        self._emit("capture.state", self.capture_state())

    def _session_key(self) -> str:
        """This session's key: the transcript database's stem, and its recording folder's name."""
        metadata = self._metadata
        if metadata is None:
            return ""
        return layout_key_for(metadata.started_at, metadata.session_id)

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
