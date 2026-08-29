"""Supervise the GStreamer subprocess that writes the video (D-022).

The whole job is lifetime: start it, notice when it dies, and — the part that matters — **end it in
a way that finalises the container**. Killing GStreamer instead of asking it to stop produces a
WebM with no duration index, which many players refuse to seek and some refuse to open. So the stop
path is `SIGINT` (which `gst-launch-1.0 -e` turns into an end-of-stream), then a wait, and only then
a kill.

The recorder must also never take the session down with it. A window closed mid-talk, a full disk,
a GStreamer that will not start — all of them end the *video* and none of them end the recording,
because the audio and its transcript are the part that cannot be recreated.
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
from typing import Final

from .pipeline import PipelineSpec

logger = logging.getLogger(__name__)

#: How long to let GStreamer finalise the container after SIGINT before killing it.
FINALISE_TIMEOUT_S: Final = 8.0

#: Poll interval for the supervisor thread.
WATCH_INTERVAL_S: Final = 0.5

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
        log_dir: Path | None = None,
    ) -> None:
        self.spec = spec
        self.state = RecorderState(video_path=spec.video_path, preview_path=spec.preview_path)
        self._portal_fd = portal_fd
        self._on_stopped = on_stopped
        self._log_dir = log_dir
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
                stderr=subprocess.PIPE,
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
        self._close_portal_fd()

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
        """Notice the process ending on its own — which almost always means the window closed."""
        process = self._process
        if process is None:
            return

        while not self._stopping.is_set():
            if process.poll() is not None:
                break
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
        self._write_log()
        if self._on_stopped is not None:
            try:
                self._on_stopped(self.state)
            except Exception:  # noqa: BLE001 - a notification must not kill the watcher
                logger.exception("Recorder stop notification failed")

    def _drain_stderr(self) -> str:
        process = self._process
        if process is None or process.stderr is None:
            return ""
        try:
            text = process.stderr.read() or ""
        except (OSError, ValueError):
            return ""
        text = text.strip()[:STDERR_LIMIT]
        if text:
            self._stderr.append(text)
        return text

    def _write_log(self) -> None:
        """Keep GStreamer's own complaint, which is the only thing that explains a failure."""
        if not self._stderr or self._log_dir is None:
            return
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            (self._log_dir / "capture.log").write_text("\n".join(self._stderr), encoding="utf-8")
        except OSError:
            logger.debug("Could not write the capture log", exc_info=True)

    def _close_portal_fd(self) -> None:
        """Release the portal's descriptor once nothing is reading it."""
        fd, self._portal_fd = self._portal_fd, None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            pass
