"""A WAV file replayed at real-time speed, as if it were a live device (BE §19.1).

This is the most useful thing in the audio layer. A saved lecture recording is reproducible; a live
microphone is not, and debugging commit logic against non-reproducible input is close to impossible.
Everything downstream — the streaming engine especially — is developed against this source.

Two modes:

* **Real time** (default) — frames are emitted on a wall-clock schedule, so timing behaviour,
  backpressure, and the commit timeout all behave exactly as they would live.
* **Fast** — frames are emitted as quickly as the consumer accepts them, for tests and for the
  accelerated soak run. ``speed`` scales the schedule; ``0`` means "as fast as possible".
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path

import numpy as np

from ..formats import DTYPE, SAMPLE_RATE, to_canonical
from .base import AudioSource, ErrorCallback, FrameCallback, SourceInfo

logger = logging.getLogger(__name__)


class WavFileSource(AudioSource):
    """Reads a WAV file, converts it to the canonical format, and emits frames on a schedule."""

    def __init__(
        self,
        path: str | Path,
        frame_ms: int = 32,
        speed: float = 1.0,
        loop: bool = False,
    ) -> None:
        super().__init__(frame_ms=frame_ms)
        self._path = Path(path)
        self._speed = speed
        self._loop = loop
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._samples: np.ndarray | None = None
        self._source_rate = SAMPLE_RATE

    # -- lifecycle -----------------------------------------------------------------

    def start(self, on_frame: FrameCallback, on_error: ErrorCallback | None = None) -> None:
        """Load the file and begin emitting frames on a background thread."""
        if self.is_running:
            raise RuntimeError("Source is already running")

        self._on_frame = on_frame
        self._on_error = on_error
        self._samples = self._load()
        self._stop.clear()
        self._running.set()

        self._thread = threading.Thread(target=self._run, name="wav-file-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop emitting and join the reader thread."""
        self._stop.set()
        self._running.clear()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    @property
    def info(self) -> SourceInfo:
        """Describe the file being replayed."""
        return SourceInfo(
            id="file",
            name=self._path.name,
            kind="file",
            sample_rate=SAMPLE_RATE,
            frame_ms=self._frame_ms,
        )

    # -- introspection -------------------------------------------------------------

    @property
    def duration_seconds(self) -> float:
        """Length of the loaded audio. Zero before :meth:`start`."""
        if self._samples is None:
            return 0.0
        return self._samples.size / float(SAMPLE_RATE)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until playback finishes. Returns whether it finished within ``timeout``.

        Tests use this instead of sleeping for a guessed duration.
        """
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    # -- internals -----------------------------------------------------------------

    def _load(self) -> np.ndarray:
        """Read the WAV file and convert it to canonical format."""
        if not self._path.is_file():
            raise FileNotFoundError(
                f"No audio file at {self._path}. Point audio.file_path at a readable WAV file."
            )

        with wave.open(str(self._path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            self._source_rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())

        dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
        if dtype is None:
            raise ValueError(
                f"Unsupported WAV sample width of {width} bytes in {self._path.name}. "
                "Convert the file to 16-bit PCM."
            )

        samples = np.frombuffer(raw, dtype=dtype)
        canonical = to_canonical(samples, self._source_rate, channels=channels)
        logger.info(
            "Loaded %s: %.1f s, %d Hz → %d Hz, %d channel(s)",
            self._path.name,
            canonical.size / SAMPLE_RATE,
            self._source_rate,
            SAMPLE_RATE,
            channels,
        )
        return canonical

    def _run(self) -> None:
        """Emit frames until the file ends or the source is stopped."""
        samples = self._samples
        if samples is None:
            self._fail(RuntimeError("Source started without loaded audio"))
            return

        frame = self._frame_samples
        interval = (frame / SAMPLE_RATE) / self._speed if self._speed > 0 else 0.0
        next_deadline = time.monotonic()

        try:
            while not self._stop.is_set():
                position = 0
                while position < samples.size and not self._stop.is_set():
                    chunk = samples[position : position + frame]
                    position += frame

                    if chunk.size < frame:
                        # Pad the final short frame so every consumer sees a uniform size.
                        chunk = np.concatenate([chunk, np.zeros(frame - chunk.size, dtype=DTYPE)])

                    self._emit(chunk)

                    if interval > 0.0:
                        next_deadline += interval
                        delay = next_deadline - time.monotonic()
                        if delay > 0:
                            self._stop.wait(delay)
                        else:
                            # Consumer fell behind. Do not try to catch up by bursting — that would
                            # mask exactly the backpressure this source exists to reproduce.
                            next_deadline = time.monotonic()

                if not self._loop:
                    break
        except Exception as exc:  # noqa: BLE001 - report, never kill the thread silently
            logger.exception("WAV file source failed")
            self._fail(exc)
            return

        self._fail(None)
