"""Write the captured stream to a WAV file while a session records (D-021).

**This is the one component in `recorded` mode that must not fail.** Everything downstream — the
transcription pass, a re-run after a failure, the muxing in `window` mode — can be retried from the
file, and none of it can be retried without one. So the design is boring on purpose: append frames
as they arrive, keep the header truthful, and never hold anything in memory that a crash would lose.

Three properties are worth stating because each is a decision rather than an accident.

**Frames are written as they arrive, not buffered to the end.** A crash then leaves a file that is
*short* rather than a file that is *corrupt*, and a short recording of a talk is worth a great deal
more than none of it.

**The RIFF header is rewritten on every flush, not only on close.** A WAV header carries two byte
counts, and a file whose header says zero is unplayable no matter how much audio follows it. Keeping
them current costs two four-byte seeks per flush and means the file on disk is openable at any
moment — including by the user, mid-recording, to check that something is actually being captured.

**There is a duration cap.** A toggle is easy to forget, and an unattended microphone will fill a
disk overnight. Reaching the cap stops the sink cleanly and reports it; it does not truncate
silently, because a recording that stopped without saying so is indistinguishable from one that
failed.
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from pathlib import Path
from typing import Final

import numpy as np

from ..audio.formats import CHANNELS, SAMPLE_RATE

logger = logging.getLogger(__name__)

#: 16-bit PCM. Chosen over float32 for the file even though the pipeline is float32 internally:
#: it halves the size, every tool on the machine opens it, and 16 bits is already well beyond what
#: a speech model can use. The conversion is the only lossy step and it is inaudible.
SAMPLE_WIDTH: Final = 2

#: Rewrite the header at most this often. Every flush is two seeks; at 32 ms frames that would be
#: 30 header rewrites a second for no benefit.
HEADER_INTERVAL_S: Final = 2.0

_RIFF_HEADER_SIZE: Final = 44


class SinkError(RuntimeError):
    """The recording could not be written. The message names the path and the reason."""


class RecordingLimitReached(SinkError):
    """The configured duration cap was reached. The audio written so far is intact."""


class WavSink:
    """An incrementally-written, always-openable 16-bit PCM WAV file.

    Thread-safe: ``write`` is called from the capture thread and ``close`` from whichever thread
    stops the session, and a partially-rewritten header read by a third is exactly the corruption
    this class exists to avoid.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        max_minutes: float | None = None,
    ) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_samples = (
            int(max_minutes * 60 * sample_rate) if max_minutes and max_minutes > 0 else None
        )

        self._lock = threading.Lock()
        self._samples = 0
        #: Monotonic time of the first frame written. **Not** of the sink being opened: the sink is
        #: created before the audio source is started, and the gap between them is not audio. This
        #: is sample zero of the file, which is what the video has to be aligned against.
        self._first_write_at = 0.0
        self._samples_at_last_header = 0
        self._closed = False
        self._limit_reported = False

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("wb")
        except OSError as exc:
            raise SinkError(f"Could not open {self.path} for recording: {exc}") from exc

        # A valid, zero-length header before a single frame arrives, so the file is never in a
        # state where it exists and cannot be opened.
        self._write_header()

    # -- properties ------------------------------------------------------------------

    @property
    def samples(self) -> int:
        """Frames written so far."""
        return self._samples

    @property
    def first_write_monotonic(self) -> float:
        """When the first frame arrived, or 0.0 if none has. The file's own time zero."""
        return self._first_write_at

    @property
    def duration_s(self) -> float:
        """Seconds of audio written so far."""
        return self._samples / float(self.sample_rate)

    @property
    def bytes_written(self) -> int:
        """Size of the file on disk, header included."""
        return _RIFF_HEADER_SIZE + self._samples * self.channels * SAMPLE_WIDTH

    @property
    def is_closed(self) -> bool:
        return self._closed

    # -- writing ---------------------------------------------------------------------

    def write(self, frame: np.ndarray) -> bool:
        """Append one frame of canonical float32 audio. Returns whether the cap was reached.

        Never raises on a full disk or a vanished file — capture must not die because storage did.
        The failure is logged once and the sink stops accepting audio, which the session surfaces
        through the size figure ceasing to climb.
        """
        if self._closed:
            return True

        with self._lock:
            if self.max_samples is not None and self._samples >= self.max_samples:
                return self._report_limit()

            block = np.asarray(frame, dtype=np.float32).reshape(-1)
            if self.max_samples is not None:
                room = self.max_samples - self._samples
                if block.size > room:
                    block = block[:room]

            try:
                self._handle.write(_to_pcm16(block))
            except OSError as exc:
                # Logged at error and then swallowed: a capture thread that raises kills the
                # session, and losing the *rest* of a recording to a full disk is worse than losing
                # the part that would not fit.
                logger.error("Recording write to %s failed: %s", self.path, exc)
                self._closed = True
                return True

            if self._first_write_at == 0.0 and block.size:
                self._first_write_at = time.monotonic()

            self._samples += block.size
            self._maybe_refresh_header()

            if self.max_samples is not None and self._samples >= self.max_samples:
                return self._report_limit()
        return False

    def close(self) -> Path:
        """Finalise the header and close the file. Safe to call more than once."""
        with self._lock:
            if self._closed:
                return self.path
            self._closed = True
            try:
                self._write_header()
                self._handle.close()
            except OSError as exc:
                raise SinkError(f"Could not finalise {self.path}: {exc}") from exc
        logger.info(
            "Recording closed: %s (%.1f s, %.1f MB)",
            self.path,
            self.duration_s,
            self.bytes_written / 1e6,
        )
        return self.path

    def discard(self) -> None:
        """Close and delete. For a recording that is not worth keeping — never for a failed pass."""
        try:
            self.close()
        except SinkError:
            pass
        self.path.unlink(missing_ok=True)

    # -- internals -------------------------------------------------------------------

    def _report_limit(self) -> bool:
        if not self._limit_reported:
            self._limit_reported = True
            logger.warning(
                "Recording %s reached its %.0f-minute cap; audio so far is intact.",
                self.path,
                (self.max_samples or 0) / (60 * self.sample_rate),
            )
        return True

    def _maybe_refresh_header(self) -> None:
        interval = int(HEADER_INTERVAL_S * self.sample_rate)
        if self._samples - self._samples_at_last_header < interval:
            return
        position = self._handle.tell()
        try:
            self._write_header()
            self._handle.seek(position)
        except OSError as exc:
            logger.error("Recording header refresh on %s failed: %s", self.path, exc)
            return
        self._samples_at_last_header = self._samples

    def _write_header(self) -> None:
        """Write a 44-byte canonical RIFF/WAVE header for the samples written so far."""
        data_bytes = self._samples * self.channels * SAMPLE_WIDTH
        byte_rate = self.sample_rate * self.channels * SAMPLE_WIDTH
        block_align = self.channels * SAMPLE_WIDTH

        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_bytes,
            b"WAVE",
            b"fmt ",
            16,  # PCM chunk size
            1,  # PCM format
            self.channels,
            self.sample_rate,
            byte_rate,
            block_align,
            SAMPLE_WIDTH * 8,
            b"data",
            data_bytes,
        )
        self._handle.seek(0)
        self._handle.write(header)
        self._handle.flush()


def _to_pcm16(block: np.ndarray) -> bytes:
    """Canonical float32 to little-endian 16-bit PCM.

    Clipped before scaling, not after. Scaling first lets a sample slightly over 1.0 wrap to a
    large negative value, which is an audible click rather than the inaudible flattening that
    clipping produces — and a click in a recording is indistinguishable from a hardware fault.
    """
    clipped = np.clip(block, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()
