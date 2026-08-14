"""Buffer trimming and timestamp rebasing (BE §7.4, §7.5).

Two of the four things BE §19.2 names as needing unit coverage. Rebasing especially: the
architecture document says outright that this is the bug you will hit.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.services.asr.contract import WordToken
from app.services.audio.formats import SAMPLE_RATE
from app.services.streaming import StreamBuffer


def audio(seconds: float, value: float = 0.5) -> np.ndarray:
    return np.full(int(seconds * SAMPLE_RATE), value, dtype=np.float32)


class TestAccumulation:
    def test_appending_grows_the_buffer(self) -> None:
        buffer = StreamBuffer()
        buffer.append(audio(1.0))
        buffer.append(audio(0.5))
        assert buffer.duration == pytest.approx(1.5)

    def test_an_empty_frame_changes_nothing(self) -> None:
        buffer = StreamBuffer()
        buffer.append(np.zeros(0, dtype=np.float32))
        assert buffer.duration == 0.0

    def test_session_seconds_counts_all_audio_ever_seen(self) -> None:
        """Derived from audio consumed, not wall-clock, so tests are deterministic."""
        buffer = StreamBuffer()
        for _ in range(4):
            buffer.append(audio(0.5))
        buffer.trim_to(1.5)
        assert buffer.session_seconds == pytest.approx(2.0)
        assert buffer.duration < 2.0


class TestRebasing:
    def test_before_any_trim_relative_equals_absolute(self) -> None:
        buffer = StreamBuffer()
        buffer.append(audio(3.0))
        assert buffer.to_absolute(1.5) == pytest.approx(1.5)

    def test_after_a_trim_absolute_time_is_offset(self) -> None:
        """The whole bug in one assertion: the buffer start moved, so timestamps must move."""
        buffer = StreamBuffer(retained_context_s=0.0)
        buffer.append(audio(10.0))
        buffer.trim_to(4.0)

        assert buffer.buffer_start_absolute == pytest.approx(4.0)
        assert buffer.to_absolute(1.0) == pytest.approx(5.0)

    def test_to_relative_inverts_to_absolute(self) -> None:
        buffer = StreamBuffer(retained_context_s=0.0)
        buffer.append(audio(10.0))
        buffer.trim_to(3.0)
        assert buffer.to_relative(buffer.to_absolute(2.0)) == pytest.approx(2.0)

    def test_rebase_shifts_every_word(self) -> None:
        buffer = StreamBuffer(retained_context_s=0.0)
        buffer.append(audio(20.0))
        buffer.trim_to(12.0)

        rebased = buffer.rebase(
            [
                WordToken(text="the", start=0.0, end=0.3, confidence=0.9),
                WordToken(text="matrix", start=0.3, end=0.9),
            ]
        )
        assert [word.start for word in rebased] == pytest.approx([12.0, 12.3])
        assert [word.end for word in rebased] == pytest.approx([12.3, 12.9])

    def test_rebase_preserves_text_and_confidence(self) -> None:
        buffer = StreamBuffer()
        rebased = buffer.rebase([WordToken(text="matrix", start=0.0, end=0.4, confidence=0.77)])
        assert rebased[0].text == "matrix"
        assert rebased[0].confidence == pytest.approx(0.77)

    def test_rebasing_nothing_returns_nothing(self) -> None:
        assert StreamBuffer().rebase([]) == []

    def test_buffer_start_is_monotonic_across_many_trims(self) -> None:
        """A start that ever moves backwards makes every subsequent timestamp wrong."""
        buffer = StreamBuffer(retained_context_s=0.5)
        previous = 0.0
        for step in range(1, 21):
            buffer.append(audio(1.0))
            buffer.trim_to(step * 0.9)
            assert buffer.buffer_start_absolute >= previous
            previous = buffer.buffer_start_absolute

    def test_absolute_time_stays_consistent_across_trims(self) -> None:
        """The end of the buffer in absolute time must equal all audio appended so far."""
        buffer = StreamBuffer(retained_context_s=0.3)
        for step in range(1, 11):
            buffer.append(audio(1.0))
            buffer.trim_to(step - 0.5)
            assert buffer.end_absolute == pytest.approx(float(step), abs=0.01)


class TestTrimming:
    def test_trimming_removes_the_committed_audio(self) -> None:
        """Constraints C1 and C2: without this the buffer grows to hour-length."""
        buffer = StreamBuffer(retained_context_s=0.0)
        buffer.append(audio(10.0))
        removed = buffer.trim_to(6.0)

        assert removed == pytest.approx(6.0)
        assert buffer.duration == pytest.approx(4.0)

    def test_the_retained_context_tail_is_kept(self) -> None:
        """Improves recognition of the first word after the cut."""
        buffer = StreamBuffer(retained_context_s=0.75)
        buffer.append(audio(10.0))
        buffer.trim_to(6.0)

        assert buffer.buffer_start_absolute == pytest.approx(5.25)
        assert buffer.duration == pytest.approx(4.75)

    def test_trimming_behind_the_buffer_start_does_nothing(self) -> None:
        """Idempotent: re-trimming to an old point must not rewind the buffer."""
        buffer = StreamBuffer(retained_context_s=0.0)
        buffer.append(audio(10.0))
        buffer.trim_to(6.0)
        assert buffer.trim_to(2.0) == 0.0
        assert buffer.buffer_start_absolute == pytest.approx(6.0)

    def test_trimming_past_the_end_keeps_the_start_within_the_audio(self) -> None:
        """A start beyond the data would make every later timestamp wrong."""
        buffer = StreamBuffer(retained_context_s=0.0)
        buffer.append(audio(5.0))
        buffer.trim_to(500.0)

        assert buffer.buffer_start_absolute == pytest.approx(5.0)
        assert buffer.duration == pytest.approx(0.0)

    def test_repeated_trimming_keeps_memory_flat(self) -> None:
        """The soak property, in miniature: 90 minutes of audio, bounded buffer."""
        buffer = StreamBuffer(retained_context_s=0.5)
        for step in range(1, 200):
            buffer.append(audio(0.5))
            buffer.trim_to(step * 0.5 - 0.2)
        assert buffer.duration < 2.0
        assert buffer.session_seconds == pytest.approx(99.5)

    def test_hard_trim_keeps_only_the_recent_tail(self) -> None:
        buffer = StreamBuffer()
        buffer.append(audio(30.0))
        removed = buffer.hard_trim(keep_seconds=5.0)

        assert removed == pytest.approx(25.0)
        assert buffer.duration == pytest.approx(5.0)
        assert buffer.buffer_start_absolute == pytest.approx(25.0)

    def test_hard_trim_of_a_short_buffer_does_nothing(self) -> None:
        buffer = StreamBuffer()
        buffer.append(audio(2.0))
        assert buffer.hard_trim(keep_seconds=5.0) == 0.0
        assert buffer.duration == pytest.approx(2.0)


class TestClearAndReset:
    def test_clear_advances_the_start_rather_than_rewinding_time(self) -> None:
        """A model swap discards audio; it must not rewind the transcript's timeline."""
        buffer = StreamBuffer()
        buffer.append(audio(8.0))
        buffer.clear()

        assert buffer.duration == 0.0
        assert buffer.buffer_start_absolute == pytest.approx(8.0)
        assert buffer.to_absolute(0.0) == pytest.approx(8.0)

    def test_reset_returns_to_zero(self) -> None:
        buffer = StreamBuffer()
        buffer.append(audio(8.0))
        buffer.trim_to(4.0)
        buffer.reset()

        assert buffer.duration == 0.0
        assert buffer.buffer_start_absolute == 0.0
        assert buffer.session_seconds == 0.0
