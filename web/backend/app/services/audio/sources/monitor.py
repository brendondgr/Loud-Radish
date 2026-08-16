"""Capture what the machine is playing, through ``pw-record``.

Window mode records a window, and a window's sound is not the user's voice. The screen-sharing
portal carries video only (D-022), so the audio is a separate capture — and the thing to capture is
the default sink's monitor, which contains everything the machine plays and no microphone at all.

**A subprocess rather than a device.** PortAudio does not expose PipeWire's monitor sources: this
machine lists fifteen inputs through `sounddevice` and not one is a `.monitor`, while
``pactl list short sources`` shows them plainly. So there is no device to select, and the capture
runs as ``pw-record`` writing raw PCM to a pipe — the same shape as the video recorder driving
``gst-launch-1.0``, and for the same reason: the tool that can do the job is a command, not a
library binding that would drag system introspection into the virtualenv (D-011, D-022).

``pw-record`` is asked for the canonical format directly — 16 kHz, mono, float32 — so PipeWire does
the resampling and downmixing in the graph, where it belongs, and this module only has to frame the
bytes.
"""

from __future__ import annotations

import logging
import subprocess
import threading

import numpy as np

from ..formats import SAMPLE_RATE
from ..monitor import MonitorUnavailable, default_monitor
from .base import AudioSource, ErrorCallback, FrameCallback, SourceInfo

logger = logging.getLogger(__name__)

#: Bytes per sample of the format requested from ``pw-record``.
BYTES_PER_SAMPLE = 4

#: How long to wait for the process to end on its own after being asked to.
STOP_TIMEOUT_S = 2.0


class MonitorSource(AudioSource):
    """The machine's audio output, as canonical-format frames."""

    def __init__(self, node: str = "", frame_ms: int = 32) -> None:
        super().__init__(frame_ms=frame_ms)
        #: Empty means "resolve at start". Resolving here would fix the node at construction time,
        #: and the default sink changes when a dock or a headset appears — see ``audio/monitor.py``.
        self._requested = node
        self._node = ""
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------------

    def start(self, on_frame: FrameCallback, on_error: ErrorCallback | None = None) -> None:
        """Begin capturing the machine's output.

        Raises:
            MonitorUnavailable: if there is no monitor to record, or ``pw-record`` is missing. This
                is fatal to the source deliberately: silently recording nothing would produce a
                session whose transcript is empty with no indication why.
        """
        if self._process is not None:
            return

        self._node = self._requested or default_monitor()
        self._on_frame = on_frame
        self._on_error = on_error

        command = [
            "pw-record",
            f"--target={self._node}",
            f"--rate={SAMPLE_RATE}",
            "--channels=1",
            "--format=f32",
            # **Not optional.** Writing to stdout without it, `pw-record` emits an AU container —
            # a 24-byte `.snd` header ahead of the samples. Read as float32 those bytes decode to
            # NaN, and a single NaN propagates through every downstream mean, peak and RMS, so the
            # level meter reads nothing and the speech gate never opens: a capture that runs
            # perfectly and transcribes silence. Observed exactly that before this flag was added.
            "--container=raw",
            # A dash is the pipe. Everything downstream reads bytes and never touches a file.
            "-",
        ]
        try:
            self._process = subprocess.Popen(  # noqa: S603 - fixed binary, no shell
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise MonitorUnavailable(
                "pw-record could not be started, so the system's audio output cannot be recorded. "
                "Install pipewire-utils (or your distribution's equivalent), or record the "
                f"microphone instead. ({exc})"
            ) from exc

        self._running.set()
        self._reader = threading.Thread(target=self._read, name="monitor-capture", daemon=True)
        self._reader.start()
        logger.info("Recording the system's audio output from %s", self._node)

    def stop(self) -> None:
        """Stop the capture and release the process."""
        self._running.clear()
        process, self._process = self._process, None
        if process is None:
            return

        process.terminate()
        try:
            process.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=STOP_TIMEOUT_S)

        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=STOP_TIMEOUT_S)

    @property
    def info(self) -> SourceInfo:
        """Describe this source, naming the node so a silent recording can be diagnosed."""
        return SourceInfo(
            id=self._node or self._requested or "system-output",
            name=f"System output ({_short(self._node)})" if self._node else "System output",
            kind="loopback",
            sample_rate=SAMPLE_RATE,
            frame_ms=self._frame_ms,
        )

    # -- internals -----------------------------------------------------------------

    def _read(self) -> None:
        """Frame the process's stdout until it ends or the source is stopped."""
        process = self._process
        if process is None or process.stdout is None:
            return

        chunk_bytes = self._frame_samples * BYTES_PER_SAMPLE
        failure: Exception | None = None

        try:
            while self._running.is_set():
                # A short read means the stream ended; anything else blocks until a full frame is
                # available, which is what keeps frame boundaries aligned to the sample grid.
                raw = process.stdout.read(chunk_bytes)
                if not raw or len(raw) < chunk_bytes:
                    break
                self._emit(np.frombuffer(raw, dtype=np.float32).copy())
        except Exception as exc:  # noqa: BLE001 - the reason reaches the session either way
            failure = exc
        finally:
            ended_early = self._running.is_set()
            self._running.clear()

        if not ended_early:
            # An ordinary stop. Nothing to report.
            return

        if failure is None:
            failure = MonitorUnavailable(
                f"The system audio capture from {_short(self._node)} ended unexpectedly. "
                f"{self._stderr()}".strip()
            )
        logger.warning("System audio capture ended: %s", failure)
        callback = self._on_error
        if callback is not None:
            callback(failure)

    def _stderr(self) -> str:
        """Whatever ``pw-record`` said on its way out, for the failure message."""
        process = self._process
        if process is None or process.stderr is None:
            return ""
        try:
            return (process.stderr.read() or b"").decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            return ""


def _short(node: str) -> str:
    """A node name trimmed to something that fits in a status bar."""
    if not node:
        return "system output"
    return node.removesuffix(".monitor").split(".")[0]
