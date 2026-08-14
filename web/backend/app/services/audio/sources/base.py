"""The audio source interface.

Every source — a live device, a WAV file, or a synthetic generator — presents the same surface,
so the session manager wires the pipeline once and never learns which one is running. That is
what makes the file source a genuine substitute for a microphone rather than a special testing
path (BE §19.1).

A source pushes frames to a callback on its own thread. It never blocks on the consumer: that
callback writes into a ring buffer that drops oldest on overflow, satisfying constraint **C5**.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..formats import SAMPLE_RATE, frame_samples

#: Called with one canonical-format frame. Must return quickly and must not raise.
FrameCallback = Callable[[np.ndarray], None]

#: Called when the source stops unexpectedly. ``None`` means a clean end of input.
ErrorCallback = Callable[[Exception | None], None]


@dataclass(frozen=True)
class SourceInfo:
    """What a running source is, for status display and diagnostics."""

    id: str
    name: str
    kind: str
    sample_rate: int = SAMPLE_RATE
    frame_ms: int = 32

    def describe(self) -> str:
        """A compact one-line description for the status bar."""
        return f"{self.name} · {self.sample_rate // 1000} kHz"


class AudioSource(ABC):
    """Base class for anything that produces canonical-format audio frames."""

    def __init__(self, frame_ms: int = 32) -> None:
        self._frame_ms = frame_ms
        self._frame_samples = frame_samples(frame_ms)
        self._on_frame: FrameCallback | None = None
        self._on_error: ErrorCallback | None = None
        self._running = threading.Event()

    # -- lifecycle -----------------------------------------------------------------

    @abstractmethod
    def start(self, on_frame: FrameCallback, on_error: ErrorCallback | None = None) -> None:
        """Begin producing frames. Returns as soon as production has started."""

    @abstractmethod
    def stop(self) -> None:
        """Stop producing frames and release any device or file handle."""

    @property
    @abstractmethod
    def info(self) -> SourceInfo:
        """Describe this source."""

    # -- shared behaviour ----------------------------------------------------------

    @property
    def frame_samples(self) -> int:
        """Samples per emitted frame."""
        return self._frame_samples

    @property
    def frame_ms(self) -> int:
        """Frame duration in milliseconds."""
        return self._frame_ms

    @property
    def is_running(self) -> bool:
        """Whether frames are currently being produced."""
        return self._running.is_set()

    def _emit(self, frame: np.ndarray) -> None:
        """Deliver one frame to the consumer, swallowing consumer errors.

        A consumer exception must not kill the capture thread. Losing capture is far worse than
        losing one frame, and the alternative — a silently dead capture thread — is the exact
        failure this pipeline is built to avoid.
        """
        callback = self._on_frame
        if callback is None:
            return
        try:
            callback(frame)
        except Exception:  # noqa: BLE001 - deliberately broad; see docstring
            import logging

            logging.getLogger(__name__).exception("Frame consumer raised; capture continues")

    def _fail(self, error: Exception | None) -> None:
        """Report that the source has stopped, cleanly or otherwise."""
        self._running.clear()
        if self._on_error is not None:
            try:
                self._on_error(error)
            except Exception:  # noqa: BLE001 - an error handler that raises must not cascade
                import logging

                logging.getLogger(__name__).exception("Source error handler raised")

    def __enter__(self) -> AudioSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
