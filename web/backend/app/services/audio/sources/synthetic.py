"""A synthetic source producing scripted silence and tone, for tests.

Nothing about the pipeline should be able to tell this apart from a device. It exists so tests can
produce an exact, repeatable pattern of speech and silence — a continuous two-minute stretch with no
pause, or a thirty-second silence — without shipping audio fixtures for every case.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from ..formats import DTYPE, SAMPLE_RATE
from .base import AudioSource, ErrorCallback, FrameCallback, SourceInfo


@dataclass(frozen=True)
class Span:
    """A stretch of generated audio."""

    seconds: float
    #: ``0.0`` is silence. Anything above the VAD threshold reads as speech.
    amplitude: float = 0.0
    frequency_hz: float = 220.0


def speech(seconds: float, amplitude: float = 0.25) -> Span:
    """A span loud enough to register as speech."""
    return Span(seconds=seconds, amplitude=amplitude)


def silence(seconds: float) -> Span:
    """A span of digital silence."""
    return Span(seconds=seconds, amplitude=0.0)


class SyntheticSource(AudioSource):
    """Emits frames generated from a list of :class:`Span` values."""

    def __init__(
        self,
        spans: list[Span],
        frame_ms: int = 32,
        speed: float = 0.0,
        loop: bool = False,
    ) -> None:
        super().__init__(frame_ms=frame_ms)
        self._spans = list(spans)
        self._speed = speed
        self._loop = loop
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, on_frame: FrameCallback, on_error: ErrorCallback | None = None) -> None:
        """Begin emitting generated frames."""
        if self.is_running:
            raise RuntimeError("Source is already running")
        self._on_frame = on_frame
        self._on_error = on_error
        self._stop.clear()
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="synthetic-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop emitting and join the generator thread."""
        self._stop.set()
        self._running.clear()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    @property
    def info(self) -> SourceInfo:
        """Describe this synthetic source."""
        return SourceInfo(
            id="synthetic",
            name="Synthetic audio",
            kind="file",
            sample_rate=SAMPLE_RATE,
            frame_ms=self._frame_ms,
        )

    def wait(self, timeout: float | None = None) -> bool:
        """Block until generation finishes."""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def render(self) -> np.ndarray:
        """Return the whole scripted signal as one array, for offline assertions."""
        parts = [_render_span(span) for span in self._spans]
        return np.concatenate(parts) if parts else np.zeros(0, dtype=DTYPE)

    def _run(self) -> None:
        """Emit the rendered signal frame by frame."""
        signal = self.render()
        frame = self._frame_samples
        interval = (frame / SAMPLE_RATE) / self._speed if self._speed > 0 else 0.0
        next_deadline = time.monotonic()

        while not self._stop.is_set():
            for start in range(0, signal.size, frame):
                if self._stop.is_set():
                    break
                chunk = signal[start : start + frame]
                if chunk.size < frame:
                    chunk = np.concatenate([chunk, np.zeros(frame - chunk.size, dtype=DTYPE)])
                self._emit(chunk)
                if interval > 0.0:
                    next_deadline += interval
                    delay = next_deadline - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                    else:
                        next_deadline = time.monotonic()
            if not self._loop:
                break

        self._fail(None)


def _render_span(span: Span) -> np.ndarray:
    """Render one span to samples."""
    count = int(round(span.seconds * SAMPLE_RATE))
    if count <= 0:
        return np.zeros(0, dtype=DTYPE)
    if span.amplitude == 0.0:
        return np.zeros(count, dtype=DTYPE)
    t = np.arange(count, dtype=np.float32) / SAMPLE_RATE
    return (span.amplitude * np.sin(2.0 * np.pi * span.frequency_hz * t)).astype(DTYPE)
