"""The capture ring buffer — the mechanism behind constraint C5 (BE §4.4).

Capture must never block on transcription. When the consumer falls behind, the oldest audio is
overwritten and the loss is *counted*. Silent loss is the failure mode this design exists to avoid,
so the counter is tested as carefully as the buffering.
"""

from __future__ import annotations

import threading

import numpy as np
import pytest
from app.services.audio.formats import SAMPLE_RATE
from app.services.audio.ring_buffer import AudioRingBuffer


def ramp(count: int, start: int = 0) -> np.ndarray:
    """A recognisable sequence, so ordering errors are visible in a failure message."""
    return np.arange(start, start + count, dtype=np.float32)


@pytest.fixture
def buffer() -> AudioRingBuffer:
    # 100 samples of capacity, expressed in seconds.
    return AudioRingBuffer(capacity_seconds=100 / SAMPLE_RATE)


class TestBasicBuffering:
    def test_capacity_is_derived_from_seconds(self) -> None:
        one_second = AudioRingBuffer(capacity_seconds=1.0)
        assert one_second.capacity_samples == SAMPLE_RATE
        assert one_second.capacity_seconds == pytest.approx(1.0)

    def test_a_non_positive_capacity_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            AudioRingBuffer(capacity_seconds=0)

    def test_reads_return_samples_in_write_order(self, buffer: AudioRingBuffer) -> None:
        buffer.write(ramp(10))
        assert buffer.read(10) == pytest.approx(ramp(10))

    def test_reading_more_than_available_returns_what_there_is(
        self, buffer: AudioRingBuffer
    ) -> None:
        buffer.write(ramp(5))
        assert buffer.read(50).size == 5

    def test_reading_an_empty_buffer_returns_an_empty_array(self, buffer: AudioRingBuffer) -> None:
        """The consumer polls; it must not have to special-case an empty buffer."""
        result = buffer.read(10)
        assert result.size == 0
        assert result.dtype == np.float32

    def test_read_with_no_count_drains_everything(self, buffer: AudioRingBuffer) -> None:
        buffer.write(ramp(30))
        assert buffer.read().size == 30
        assert len(buffer) == 0

    def test_peek_does_not_consume(self, buffer: AudioRingBuffer) -> None:
        buffer.write(ramp(10))
        assert buffer.peek(4) == pytest.approx(ramp(4))
        assert len(buffer) == 10

    def test_writing_nothing_is_harmless(self, buffer: AudioRingBuffer) -> None:
        assert buffer.write(np.zeros(0, dtype=np.float32)) == 0


class TestWrapping:
    def test_data_written_across_the_wrap_point_reads_back_in_order(
        self, buffer: AudioRingBuffer
    ) -> None:
        buffer.write(ramp(80))
        buffer.read(80)
        buffer.write(ramp(40, start=100))  # wraps past the end of the underlying array
        assert buffer.read(40) == pytest.approx(ramp(40, start=100))

    def test_repeated_wrapping_stays_consistent(self, buffer: AudioRingBuffer) -> None:
        for cycle in range(20):
            buffer.write(ramp(37, start=cycle * 37))
            assert buffer.read(37) == pytest.approx(ramp(37, start=cycle * 37))
        assert buffer.stats().dropped_samples == 0


class TestOverflow:
    def test_the_oldest_audio_is_overwritten_not_the_newest(self, buffer: AudioRingBuffer) -> None:
        """Dropping the newest audio would mean discarding the words just spoken."""
        buffer.write(ramp(100))
        buffer.write(ramp(10, start=1000))
        tail = buffer.read()
        assert tail[-10:] == pytest.approx(ramp(10, start=1000))

    def test_overflow_is_counted(self, buffer: AudioRingBuffer) -> None:
        buffer.write(ramp(100))
        assert buffer.write(ramp(30)) == 30

        stats = buffer.stats()
        assert stats.dropped_samples == 30
        assert stats.dropped_events == 1

    def test_a_write_larger_than_the_whole_buffer_keeps_its_tail(
        self, buffer: AudioRingBuffer
    ) -> None:
        dropped = buffer.write(ramp(250))
        assert dropped == 150
        assert buffer.read() == pytest.approx(ramp(100, start=150))

    def test_a_healthy_session_drops_nothing(self, buffer: AudioRingBuffer) -> None:
        for _ in range(50):
            buffer.write(ramp(20))
            buffer.read(20)
        assert buffer.stats().dropped_events == 0

    def test_dropped_seconds_converts_the_sample_count(self) -> None:
        small = AudioRingBuffer(capacity_seconds=0.5)
        small.write(np.zeros(SAMPLE_RATE, dtype=np.float32))
        assert small.stats().dropped_seconds == pytest.approx(0.5, abs=0.01)


class TestStats:
    def test_fill_ratio_reports_how_full_the_buffer_is(self, buffer: AudioRingBuffer) -> None:
        buffer.write(ramp(25))
        assert buffer.stats().fill_ratio == pytest.approx(0.25)

    def test_total_written_counts_everything_including_dropped(
        self, buffer: AudioRingBuffer
    ) -> None:
        buffer.write(ramp(150))
        assert buffer.stats().total_written == 150

    def test_clear_discards_audio_but_keeps_the_drop_history(self, buffer: AudioRingBuffer) -> None:
        """Drops describe the session, not the buffer's current contents."""
        buffer.write(ramp(150))
        buffer.clear()
        assert len(buffer) == 0
        assert buffer.stats().dropped_samples == 50

    def test_reset_stats_zeroes_the_counters(self, buffer: AudioRingBuffer) -> None:
        buffer.write(ramp(150))
        buffer.reset_stats()
        stats = buffer.stats()
        assert stats.dropped_samples == 0
        assert stats.total_written == 0


class TestConcurrency:
    def test_a_slow_consumer_never_blocks_the_producer(self) -> None:
        """Constraint C5: the writer completes regardless of how far behind the reader is."""
        buffer = AudioRingBuffer(capacity_seconds=0.1)
        written = 0

        def produce() -> None:
            nonlocal written
            for _ in range(400):
                buffer.write(np.ones(160, dtype=np.float32))
                written += 160

        producer = threading.Thread(target=produce)
        producer.start()
        producer.join(timeout=5.0)

        assert not producer.is_alive(), "the producer blocked on a full buffer"
        assert written == 400 * 160
        assert buffer.stats().dropped_samples > 0

    def test_concurrent_readers_and_writers_do_not_corrupt_the_buffer(self) -> None:
        buffer = AudioRingBuffer(capacity_seconds=1.0)
        stop = threading.Event()
        failures: list[str] = []

        def produce() -> None:
            for _ in range(500):
                buffer.write(np.full(160, 0.5, dtype=np.float32))
            stop.set()

        def consume() -> None:
            while not stop.is_set() or len(buffer):
                chunk = buffer.read(320)
                if chunk.size and not np.all(chunk == 0.5):
                    failures.append("read a value never written")

        threads = [threading.Thread(target=produce), threading.Thread(target=consume)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)

        assert not failures
