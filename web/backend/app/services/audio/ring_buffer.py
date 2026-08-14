"""A fixed-capacity circular buffer between capture and the rest of the pipeline (BE §4.4).

This is what enforces constraint **C5** — capture never blocks on transcription. When the
consumer falls behind, the oldest audio is overwritten rather than accumulating, and the
overwrite is counted.

That counter matters as much as the buffer does. A dropped-audio event means words were lost, and
losing words silently is the failure mode this whole design exists to avoid. The count is
surfaced in health telemetry and should be zero in a healthy session.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

from .formats import DTYPE, SAMPLE_RATE, duration_seconds


@dataclass(frozen=True)
class RingBufferStats:
    """A snapshot of buffer health, safe to serialise into a status event."""

    capacity_samples: int
    available_samples: int
    dropped_samples: int
    dropped_events: int
    total_written: int

    @property
    def fill_ratio(self) -> float:
        """How full the buffer is, 0.0–1.0. A ratio that climbs and stays high is backpressure."""
        if self.capacity_samples == 0:
            return 0.0
        return self.available_samples / self.capacity_samples

    @property
    def dropped_seconds(self) -> float:
        """Total audio lost to overwriting, in seconds."""
        return duration_seconds(self.dropped_samples)


class AudioRingBuffer:
    """A thread-safe circular buffer of float32 samples.

    The writer is the capture thread and must never wait; the reader is the ASR worker. Both sides
    are guarded by one lock held only for the duration of a memory copy, which is short enough that
    the writer is never meaningfully delayed.
    """

    def __init__(self, capacity_seconds: float = 30.0, sample_rate: int = SAMPLE_RATE) -> None:
        if capacity_seconds <= 0:
            raise ValueError("Ring buffer capacity must be positive")
        self._capacity = int(round(capacity_seconds * sample_rate))
        self._sample_rate = sample_rate
        self._data = np.zeros(self._capacity, dtype=DTYPE)
        self._write = 0
        self._available = 0
        self._dropped_samples = 0
        self._dropped_events = 0
        self._total_written = 0
        self._lock = threading.Lock()

    # -- properties ----------------------------------------------------------------

    @property
    def capacity_samples(self) -> int:
        """Total sample capacity."""
        return self._capacity

    @property
    def capacity_seconds(self) -> float:
        """Total capacity in seconds."""
        return duration_seconds(self._capacity, self._sample_rate)

    def __len__(self) -> int:
        """Samples currently readable."""
        with self._lock:
            return self._available

    # -- writing -------------------------------------------------------------------

    def write(self, samples: np.ndarray) -> int:
        """Append ``samples``, overwriting the oldest audio if necessary.

        Returns:
            How many samples were dropped by this write. Non-zero is a health event, not a
            normal condition.
        """
        array = np.ascontiguousarray(samples, dtype=DTYPE).ravel()
        if array.size == 0:
            return 0

        with self._lock:
            self._total_written += array.size

            if array.size >= self._capacity:
                # The incoming block alone exceeds capacity: keep only its tail.
                dropped = self._available + (array.size - self._capacity)
                self._data[:] = array[-self._capacity :]
                self._write = 0
                self._available = self._capacity
                self._record_drop(dropped)
                return dropped

            end = self._write + array.size
            if end <= self._capacity:
                self._data[self._write : end] = array
            else:
                split = self._capacity - self._write
                self._data[self._write :] = array[:split]
                self._data[: end - self._capacity] = array[split:]
            self._write = end % self._capacity

            overflow = max(0, self._available + array.size - self._capacity)
            self._available = min(self._capacity, self._available + array.size)
            self._record_drop(overflow)
            return overflow

    def _record_drop(self, dropped: int) -> None:
        """Record a drop. Caller holds the lock."""
        if dropped > 0:
            self._dropped_samples += dropped
            self._dropped_events += 1

    # -- reading -------------------------------------------------------------------

    def read(self, count: int | None = None) -> np.ndarray:
        """Remove and return up to ``count`` samples, oldest first.

        ``None`` drains everything available. Returns an empty array when nothing is buffered, so
        callers poll without special-casing.
        """
        with self._lock:
            take = self._available if count is None else min(count, self._available)
            if take <= 0:
                return np.zeros(0, dtype=DTYPE)

            start = (self._write - self._available) % self._capacity
            out = self._slice(start, take)
            self._available -= take
            return out

    def peek(self, count: int | None = None) -> np.ndarray:
        """Return up to ``count`` samples without consuming them."""
        with self._lock:
            take = self._available if count is None else min(count, self._available)
            if take <= 0:
                return np.zeros(0, dtype=DTYPE)
            start = (self._write - self._available) % self._capacity
            return self._slice(start, take)

    def _slice(self, start: int, count: int) -> np.ndarray:
        """Copy ``count`` samples from ``start``, wrapping. Caller holds the lock."""
        end = start + count
        if end <= self._capacity:
            return self._data[start:end].copy()
        first = self._capacity - start
        out = np.empty(count, dtype=DTYPE)
        out[:first] = self._data[start:]
        out[first:] = self._data[: count - first]
        return out

    # -- maintenance ---------------------------------------------------------------

    def clear(self) -> None:
        """Discard buffered audio. Drop counters survive: they describe the session, not
        the buffer."""
        with self._lock:
            self._write = 0
            self._available = 0

    def reset_stats(self) -> None:
        """Zero the drop counters. Called when a new session starts, never mid-session."""
        with self._lock:
            self._dropped_samples = 0
            self._dropped_events = 0
            self._total_written = 0

    def stats(self) -> RingBufferStats:
        """A consistent snapshot of buffer health."""
        with self._lock:
            return RingBufferStats(
                capacity_samples=self._capacity,
                available_samples=self._available,
                dropped_samples=self._dropped_samples,
                dropped_events=self._dropped_events,
                total_written=self._total_written,
            )
