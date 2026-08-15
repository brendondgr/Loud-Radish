"""Live capture from a microphone or system loopback device.

Implemented against the same :class:`AudioSource` interface as the file source, so nothing
downstream can tell the two apart.

.. warning::
   **This path cannot be verified in a headless environment.** There is no audio device and no host
   audio service in CI or in the development worktree, so its unit coverage uses a fake
   ``sounddevice`` module. Confirming that real audio arrives — that it is not silence, not clipped,
   and from the intended device — is a manual step on the user's own machine, tracked in
   ``docs/checklist.md`` Part 4.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

from ..devices import AudioDevice, default_device, find_device
from ..formats import SAMPLE_RATE, to_canonical
from .base import AudioSource, ErrorCallback, FrameCallback, SourceInfo

logger = logging.getLogger(__name__)


class DeviceUnavailableError(RuntimeError):
    """Raised when the requested device cannot be opened."""


class DeviceSource(AudioSource):
    """Captures from a host audio device via PortAudio.

    The device is opened at its own preferred sample rate and converted to canonical format in the
    stream callback, rather than asking the host to resample. Host resamplers vary in quality and
    some silently refuse the rate, giving audio that sounds fine and transcribes badly.
    """

    def __init__(
        self,
        device_id: str | None = None,
        frame_ms: int = 32,
        sounddevice_module: Any | None = None,
    ) -> None:
        super().__init__(frame_ms=frame_ms)
        self._device_id = device_id
        self._sd = sounddevice_module
        self._stream: Any | None = None
        self._device: AudioDevice | None = None
        self._channels = 1
        self._device_rate = SAMPLE_RATE
        self._residual = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------------

    def start(self, on_frame: FrameCallback, on_error: ErrorCallback | None = None) -> None:
        """Open the device and begin capture."""
        if self.is_running:
            raise RuntimeError("Source is already running")

        sd = self._require_sounddevice()
        device = find_device(self._device_id)
        if device is None:
            fallback = default_device()
            raise DeviceUnavailableError(
                "The selected input is not connected any more. "
                + (
                    f"Choose another in Settings → Audio — {fallback.name} is available."
                    if fallback
                    else "No capture device is available at all."
                )
            )
        if device.kind == "file":
            raise DeviceUnavailableError(
                "The file source was selected. Use WavFileSource, not DeviceSource."
            )

        self._on_frame = on_frame
        self._on_error = on_error
        self._device = device
        self._channels = min(2, max(1, device.channels))
        self._device_rate = device.default_sample_rate
        self._residual = np.zeros(0, dtype=np.float32)

        try:
            self._stream = sd.InputStream(
                # Resolved fresh, never stored: a PortAudio index is positional and shifts
                # whenever hardware is plugged or unplugged.
                device=device.index,
                channels=self._channels,
                samplerate=self._device_rate,
                dtype="float32",
                blocksize=self._device_block_size(),
                callback=self._callback,
                finished_callback=self._finished,
            )
            self._stream.start()
        except Exception as exc:  # noqa: BLE001 - PortAudio raises varied host errors
            self._stream = None
            raise DeviceUnavailableError(
                f"Could not open {device.name} ({type(exc).__name__}). "
                "Another application may hold the device exclusively, or it may have been removed."
            ) from exc

        self._running.set()
        logger.info("Capturing from %s at %d Hz", device.name, self._device_rate)

    def stop(self) -> None:
        """Stop capture and close the device."""
        self._running.clear()
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001 - closing a removed device commonly raises
            logger.debug("Ignoring error while closing the capture stream", exc_info=True)

    @property
    def info(self) -> SourceInfo:
        """Describe the open device."""
        device = self._device
        if device is None:
            return SourceInfo(id="none", name="No device", kind="microphone")
        return SourceInfo(
            id=device.id,
            name=device.name,
            kind=device.kind,
            sample_rate=SAMPLE_RATE,
            frame_ms=self._frame_ms,
        )

    # -- internals -----------------------------------------------------------------

    def _require_sounddevice(self) -> Any:
        if self._sd is not None:
            return self._sd
        try:
            import sounddevice
        except (ImportError, OSError) as exc:
            raise DeviceUnavailableError(
                "Live capture needs the optional audio backend. "
                "Reinstall the dependencies with: uv sync"
            ) from exc
        self._sd = sounddevice
        return sounddevice

    def _device_block_size(self) -> int:
        """Device-side block size matching this source's frame duration."""
        return int(round(self._device_rate * self._frame_ms / 1000.0))

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """PortAudio stream callback. Runs on a realtime thread and must return quickly.

        Conversion and emission both happen here, but the consumer only writes into a ring buffer,
        so nothing in this path waits on transcription (constraint C5).
        """
        if status:
            # Overflow means the host dropped input before we saw it — worth knowing about.
            logger.warning("Audio device reported %s", status)

        canonical = to_canonical(np.asarray(indata), self._device_rate, channels=None)
        with self._lock:
            pending = (
                np.concatenate([self._residual, canonical]) if self._residual.size else canonical
            )
            count = (pending.size // self._frame_samples) * self._frame_samples
            whole, self._residual = pending[:count], pending[count:].copy()

        for start in range(0, count, self._frame_samples):
            self._emit(whole[start : start + self._frame_samples])

    def _finished(self) -> None:
        """PortAudio's stream-finished callback."""
        if self.is_running:
            # Finished while we still believed we were capturing: the device went away.
            self._fail(
                DeviceUnavailableError(
                    "The capture device stopped unexpectedly. It may have been unplugged."
                )
            )
