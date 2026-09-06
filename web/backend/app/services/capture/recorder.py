"""Supervise the GStreamer subprocess that writes the video (D-022).

The whole job is lifetime: start it, notice when it dies, and — the part that matters — **end it in
a way that finalises the container**. Killing GStreamer instead of asking it to stop produces a
WebM with no duration index, which many players refuse to seek and some refuse to open. So the stop
path is `SIGINT` (which `gst-launch-1.0 -e` turns into an end-of-stream), then a wait, and only then
a kill.

The recorder must also never take the session down with it. A window closed mid-talk, a full disk,
a GStreamer that will not start — all of them end the *video* and none of them end the recording,
because the audio and its transcript are the part that cannot be recreated.

**Supervision asks two questions, and it used to ask one.** "Has the process exited" is what a
`poll()` loop answers, and a pipeline that is alive and receiving nothing answers it "no" for as
long as the fault lasts. Measured against a 68-minute seminar: frames at a steady 15 fps to
882.5 s, then none at all, then a clean exit at 1765.2 s — fourteen minutes and forty-three seconds
in which the recorder was running, healthy by every check it made, and writing nothing. So the
second question is "is it still writing", answered by sampling the output file's size on the same
tick. A file that has not grown for :data:`STALL_TIMEOUT_S` is stalled, whatever the process says
about itself.

**And GStreamer's own account of it is kept.** The pipeline used to run under `-q` with stderr on a
pipe, which is two problems: the messages that would have named the cause were suppressed, and a
pipe nobody drains fills at 64 KB and blocks the process writing to it — a way of *causing* the
stall this file exists to detect. stderr goes straight to a file now, and `-q` is gone.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from .pipeline import PipelineSpec

logger = logging.getLogger(__name__)

#: How long to let GStreamer finalise the container after SIGINT before killing it.
FINALISE_TIMEOUT_S: Final = 8.0

#: Poll interval for the supervisor thread.
WATCH_INTERVAL_S: Final = 0.5

#: How long the output file may go without growing before the capture counts as stalled.
#:
#: Well above anything normal and well below anything worth losing. The encoder is constant-quality
#: with a keyframe at least every 30 frames, so even a motionless slide produces a keyframe every
#: two seconds and the muxer cuts a cluster on it; a gap of this length means buffers have stopped
#: arriving rather than that nothing is happening on screen.
STALL_TIMEOUT_S: Final = 15.0

#: How long to allow for the first bytes to reach the file before the stall clock starts.
#: A cold pipeline negotiates caps, allocates buffers and warms an encoder before it writes
#: anything, and calling that a stall would refuse every recording at the moment it began.
STALL_GRACE_S: Final = 20.0

#: Keep this much of stderr for diagnostics. A pipeline that fails prints one useful paragraph;
#: one that fails repeatedly prints it thousands of times, and the log is not the place for that.
STDERR_LIMIT: Final = 8_000


class RecorderError(RuntimeError):
    """The video recorder could not be started."""


@dataclass
class RecorderState:
    """What the recorder is doing, for the monitor pane."""

    running: bool = False
    #: Set when the stream ended on its own — almost always the captured window being closed.
    window_closed: bool = False
    #: Set when GStreamer exited non-zero.
    failed: bool = False
    error: str = ""
    video_path: str = ""
    preview_path: str = ""
    started_at: float = 0.0
    #: Monotonic time at which capture was told to end — as close to the last encoded frame as this
    #: process can observe. With the file's own duration it gives the moment the video *started*,
    #: which is what aligning it with the audio needs and which cannot be measured at the other end:
    #: spawning GStreamer and its first frame arriving are seconds apart on a bad day.
    stopped_at: float = 0.0
    #: Set while the process is alive and the output file has stopped growing. Cleared again if it
    #: resumes, because a stall that ends on its own is not a thing to keep reporting.
    stalled: bool = False
    #: Monotonic time the output file was last seen to grow. Zero until the first bytes land.
    last_growth_at: float = 0.0
    #: The largest size the output file has been seen at. The comparison the stall check makes.
    last_size: int = 0

    @property
    def stalled_seconds(self) -> float:
        """How long the output has gone without growing. Zero when it is growing, or not started."""
        if not self.running or not self.last_growth_at:
            return 0.0
        return max(0.0, time.monotonic() - self.last_growth_at)

    @property
    def bytes_written(self) -> int:
        try:
            return Path(self.video_path).stat().st_size if self.video_path else 0
        except OSError:
            return 0

    @property
    def duration_s(self) -> float:
        return max(0.0, time.monotonic() - self.started_at) if self.started_at else 0.0


class WindowRecorder:
    """One `gst-launch-1.0` process, supervised."""

    def __init__(
        self,
        spec: PipelineSpec,
        *,
        portal_fd: int | None = None,
        on_stopped: Callable[[RecorderState], None] | None = None,
        on_health: Callable[[RecorderState], None] | None = None,
        log_dir: Path | None = None,
        log_name: str = "capture.log",
    ) -> None:
        self.spec = spec
        self.state = RecorderState(video_path=spec.video_path, preview_path=spec.preview_path)
        self._portal_fd = portal_fd
        self._on_stopped = on_stopped
        #: Called when the capture stops writing, and again if it starts writing once more. The
        #: recorder only *reports*; deciding what a stall is worth doing about belongs upstream.
        self._on_health = on_health
        self._log_dir = log_dir
        #: The file GStreamer's stderr goes to. Varied per segment when a capture resumes, so a
        #: second attempt's account does not overwrite the first one's — which is the account that
        #: says why there had to be a second.
        self._log_name = log_name
        self._log_handle: IO[str] | None = None
        self._process: subprocess.Popen | None = None
        self._watcher: threading.Thread | None = None
        self._stopping = threading.Event()
        self._stderr: list[str] = []

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Launch the pipeline. Raises :class:`RecorderError` if it will not start."""
        Path(self.spec.video_path).parent.mkdir(parents=True, exist_ok=True)

        try:
            self._process = subprocess.Popen(  # noqa: S603 - argv built from a fixed vocabulary
                self.spec.args,
                stdout=subprocess.DEVNULL,
                # **A file, not a pipe, whenever there is somewhere to put it.** Nothing reads this
                # stream until the process has exited, and an undrained pipe blocks its writer at
                # 64 KB — which would hang the encoder as a *consequence* of it having something to
                # say. The file is also the log, so there is no second copy to keep in step.
                stderr=self._open_log() or subprocess.PIPE,
                text=True,
                # The portal's descriptor has to survive exec, or `pipewiresrc fd=N` opens nothing.
                pass_fds=(self._portal_fd,) if self._portal_fd is not None else (),
                # Its own process group, so a Ctrl+C in the terminal running the server does not
                # tear the recorder down before the session has finished with it.
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            raise RecorderError(f"Could not start the video recorder: {exc}") from exc

        self.state.running = True
        self.state.started_at = time.monotonic()
        self._stopping.clear()

        self._watcher = threading.Thread(target=self._watch, name="capture-watch", daemon=True)
        self._watcher.start()

        logger.info("Recording video to %s", self.spec.video_path)
        logger.debug("Pipeline: %s", self.spec.command)

        # A pipeline with a bad element exits almost immediately. Catching that here turns "the
        # video file is empty" into an error at the moment the user pressed record.
        #
        # **Only a non-zero exit counts.** A clean exit inside the settle window means the source
        # ended straight away — a window closed the instant it was chosen — which is the
        # window-closed path, not a broken pipeline. Treating any early exit as a failure was the
        # first version and it reported "stopped immediately: it exited with code 0", which is a
        # sentence that answers nothing.
        time.sleep(0.4)
        code = self._process.poll()
        if code is not None and code != 0:
            self.state.running = False
            detail = self._drain_stderr() or f"it exited with code {code}"
            raise RecorderError(f"The video recorder stopped immediately: {detail}")

    def stop(self) -> RecorderState:
        """End the recording and finalise the file. Safe to call more than once."""
        self._stopping.set()
        process = self._process
        if process is None:
            return self.state

        if process.poll() is None:
            # Stamped before the signal, not after the wait: finalising a container takes seconds
            # and encodes nothing new, so the last frame belongs to this instant rather than to
            # whenever the process finally exits.
            self.state.stopped_at = time.monotonic()
            # SIGINT, not SIGTERM: `gst-launch-1.0 -e` turns interrupt into end-of-stream, which is
            # what writes the container's index. SIGTERM just ends the process.
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGINT)
            except (OSError, ProcessLookupError):
                pass

            try:
                process.wait(timeout=FINALISE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "The video recorder did not finalise within %.0fs; killing it. "
                    "The file may not be seekable.",
                    FINALISE_TIMEOUT_S,
                )
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    process.wait(timeout=2.0)
                except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
                    pass

        self.state.running = False
        self.state.stalled = False
        self._close_portal_fd()
        # Kept on an ordinary stop as well, not only on a failure. With `-q` gone this is where a
        # capture that went quiet explains itself, and a recording that ended normally after going
        # quiet is exactly the case the log is wanted for.
        self._drain_stderr()

        watcher = self._watcher
        if watcher is not None and watcher.is_alive():
            watcher.join(timeout=2.0)

        logger.info(
            "Video recording finished: %s (%.1f MB)",
            self.spec.video_path,
            self.state.bytes_written / 1e6,
        )
        return self.state

    # -- supervision -----------------------------------------------------------------

    def _watch(self) -> None:
        """Notice the process ending on its own, and notice it going quiet without ending."""
        process = self._process
        if process is None:
            return

        while not self._stopping.is_set():
            if process.poll() is not None:
                break
            self._sample_progress()
            time.sleep(WATCH_INTERVAL_S)

        if self._stopping.is_set() or process.poll() is None:
            return

        self.state.running = False
        code = process.returncode
        detail = self._drain_stderr()

        # A clean exit that we did not ask for means the source ended: the window was closed. That
        # is an ordinary event, not a failure — people close windows.
        if code == 0:
            self.state.window_closed = True
            logger.info("The captured window closed; video recording ended.")
        else:
            self.state.failed = True
            self.state.error = detail or f"The video recorder exited with code {code}."
            logger.error("Video recording failed: %s", self.state.error)

        self._close_portal_fd()
        if self._on_stopped is not None:
            try:
                self._on_stopped(self.state)
            except Exception:  # noqa: BLE001 - a notification must not kill the watcher
                logger.exception("Recorder stop notification failed")

    def _sample_progress(self) -> None:
        """Ask the one question `poll()` cannot: is it still writing anything?

        The check is the output file's size, because that is what "recording" means from outside
        the process — and because it costs one `stat` per half-second and needs nothing of
        GStreamer, which is the party whose word is in doubt.
        """
        now = time.monotonic()
        size = self.state.bytes_written

        if size > self.state.last_size:
            self.state.last_size = size
            self.state.last_growth_at = now
            if self.state.stalled:
                self.state.stalled = False
                logger.info(
                    "The video capture is writing again after %.0fs.", now - self.state.stopped_at
                )
                self._notify_health()
            return

        # Nothing yet. The clock starts at the first bytes, or at the end of the grace window if
        # none ever arrive — a pipeline that never writes at all is stalled too, and the loudest
        # case of it.
        if not self.state.last_growth_at:
            if now - self.state.started_at < STALL_GRACE_S:
                return
            self.state.last_growth_at = self.state.started_at + STALL_GRACE_S

        if not self.state.stalled and now - self.state.last_growth_at >= STALL_TIMEOUT_S:
            self.state.stalled = True
            logger.warning(
                "The video capture has written nothing for %.0fs; treating it as stalled.",
                now - self.state.last_growth_at,
            )
            self._notify_health()

    def _notify_health(self) -> None:
        if self._on_health is None:
            return
        try:
            self._on_health(self.state)
        except Exception:  # noqa: BLE001 - a notification must not kill the watcher
            logger.exception("Recorder health notification failed")

    def _open_log(self) -> IO[str] | None:
        """Where GStreamer's own account goes. ``None`` when there is nowhere to put it."""
        if self._log_dir is None:
            return None
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._log_handle = (self._log_dir / self._log_name).open("w", encoding="utf-8")
        except OSError:
            logger.debug("Could not open the capture log", exc_info=True)
            return None
        return self._log_handle

    def _close_log(self) -> None:
        handle, self._log_handle = self._log_handle, None
        if handle is None:
            return
        try:
            handle.close()
        except OSError:
            pass

    def _drain_stderr(self) -> str:
        """GStreamer's own account, from wherever it was sent.

        **Read once the process has exited, never during a run.** Reading a live pipe blocks until
        EOF, and the file is still being appended to.
        """
        self._close_log()
        text = ""
        if self._log_dir is not None:
            try:
                path = self._log_dir / self._log_name
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        else:
            process = self._process
            if process is not None and process.stderr is not None:
                try:
                    text = process.stderr.read() or ""
                except (OSError, ValueError):
                    text = ""

        # The tail, not the head: a pipeline that fails repeatedly says the same paragraph
        # thousands of times, and the last one is the one that ended it.
        text = text.strip()[-STDERR_LIMIT:]
        if text and text not in self._stderr:
            self._stderr.append(text)
        return text

    def _close_portal_fd(self) -> None:
        """Release the portal's descriptor once nothing is reading it."""
        fd, self._portal_fd = self._portal_fd, None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            pass
