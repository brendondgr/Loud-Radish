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
import time
from typing import Any

import numpy as np

from ..formats import SAMPLE_RATE
from ..monitor import MonitorUnavailable, default_monitor
from .base import AudioSource, ErrorCallback, FrameCallback, SourceInfo

logger = logging.getLogger(__name__)

#: Bytes per sample of the format requested from ``pw-record``.
BYTES_PER_SAMPLE = 4

#: How long to wait for the process to end on its own after being asked to.
STOP_TIMEOUT_S = 2.0

#: How much audio to check before trusting the stream. One second is enough to catch a container
#: header being read as samples, which is the fault this exists for.
GUARD_SAMPLES = 16_000

#: How far the delivered byte count may drift from `rate x channels x 4 x seconds` before the
#: capture is considered mis-specified rather than merely jittery.
RATE_TOLERANCE = 0.02


def record_command(node: str, *, capture_sink: bool, destination: str = "-") -> list[str]:
    """The ``pw-record`` invocation for one node.

    **Two target forms, because two kinds of sink resolve differently**, measured rather than
    assumed. A real device's sink exposes a ``<name>.monitor`` *source* that ``pw-record`` finds
    directly. A null sink created for the application tap does not: targeting ``<name>.monitor``
    fails outright with ``defined target not found``, while targeting the sink itself with
    ``stream.capture.sink`` returns the audio linked into it — verified against a uniquely named
    sink with a 440 Hz tone, which came back as 440.0 Hz.
    """
    command = [
        "pw-record",
        f"--target={node.removesuffix('.monitor') if capture_sink else node}",
        f"--rate={SAMPLE_RATE}",
        "--channels=1",
        "--format=f32",
        # **Not optional.** Writing to stdout without it, `pw-record` emits an AU container — a
        # 24-byte `.snd` header ahead of the samples. Read as float32 those bytes decode to NaN, and
        # a single NaN propagates through every downstream mean, peak and RMS, so the level meter
        # reads nothing and the speech gate never opens: a capture that runs perfectly and
        # transcribes silence. Observed exactly that before this flag was added.
        "--container=raw",
    ]

    if capture_sink:
        # Only on the tap's path, and both properties are load-bearing there.
        #
        # `stream.capture.sink` is how a null sink's monitor is reached at all. And
        # `node.dont-fallback` is what makes a bad target visible: a stream whose named target
        # cannot be found does **not** fail by default, it connects to the default target, which
        # for a capture is the **microphone**. That is what happened. A window recording of a
        # YouTube video transcribed the viewer's own speech while every layer reported success;
        # measured after the fact, the "tap" audio correlated with the microphone at **0.92** and
        # matched its RMS to five decimal places. With these set the correlation is 0.05, and a
        # bad target fails loudly — verified: a nonexistent target now exits 1 with
        # `defined target not found` rather than quietly recording a room.
        #
        # A device sink's `<name>.monitor` is a real source that resolves on its own, and adding
        # these to that path stops it connecting at all — so it does not get them.
        command += ["-P", "{ stream.capture.sink = true node.dont-fallback = true }"]

    # A dash is the pipe. Everything downstream reads bytes and never touches a file.
    return [*command, destination]


class MonitorSource(AudioSource):
    """The machine's audio output, as canonical-format frames."""

    def __init__(self, node: str = "", frame_ms: int = 32, tap: Any = None) -> None:
        super().__init__(frame_ms=frame_ms)
        #: An open :class:`~app.services.audio.tap.ApplicationTap` whose monitor to record instead
        #: of the default sink's. Owned by the caller, which also closes it — this source records
        #: what it is pointed at and does not manage the graph.
        self._tap = tap
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

        if self._tap is not None and self._tap.is_open:
            self._node = self._tap.monitor
        else:
            self._node = self._requested or default_monitor()

        capture_sink = self._tap is not None and self._tap.is_open
        self._on_frame = on_frame
        self._on_error = on_error

        command = record_command(self._node, capture_sink=capture_sink)
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
        checked = 0
        started = time.monotonic()
        delivered = 0

        try:
            while self._running.is_set():
                # A short read means the stream ended; anything else blocks until a full frame is
                # available, which is what keeps frame boundaries aligned to the sample grid.
                raw = process.stdout.read(chunk_bytes)
                if not raw or len(raw) < chunk_bytes:
                    break
                frame = np.frombuffer(raw, dtype=np.float32).copy()
                delivered += frame.size

                # **Checked here, in the recorder, not in a test.** A test sees what this code does;
                # only the running capture sees what the machine actually sent. `pw-record` without
                # `--container=raw` writes an AU header whose bytes decode to NaN, and that capture
                # ran at the right rate for the right duration and transcribed silence — every
                # downstream mean, peak and RMS was poisoned by one NaN. Three lines catch it.
                if checked < GUARD_SAMPLES:
                    checked += frame.size
                    if not np.isfinite(frame).all():
                        raise MonitorUnavailable(
                            "The system audio capture returned values that are not finite, which "
                            "means the byte stream is not the format it was asked for. A container "
                            "header being read as samples is the usual cause."
                        )

                self._emit(frame)
        except Exception as exc:  # noqa: BLE001 - the reason reaches the session either way
            failure = exc
        finally:
            ended_early = self._running.is_set()
            self._running.clear()

        elapsed = time.monotonic() - started
        self._check_rate(delivered, elapsed)

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

    def _check_rate(self, delivered: int, elapsed: float) -> None:
        """Log when the delivered sample count does not match the wall clock.

        A capture asked for the wrong sample format still produces bytes at a plausible rate, so
        duration alone cannot tell them apart — but the *count* can. Logged rather than raised: by
        the time this is known the recording exists, and discarding a talk over a rate mismatch
        would be a worse outcome than a warning naming it.
        """
        if elapsed < 1.0 or delivered == 0:
            return
        expected = SAMPLE_RATE * elapsed
        drift = abs(delivered - expected) / expected
        if drift > RATE_TOLERANCE:
            logger.warning(
                "System audio delivered %d samples in %.1f s, %.0f%% off the %d Hz it was asked "
                "for. The capture format may not be what was requested.",
                delivered,
                elapsed,
                drift * 100,
                SAMPLE_RATE,
            )

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
