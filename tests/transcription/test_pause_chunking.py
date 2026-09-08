"""Cutting a finished recording where nobody was speaking (D-061).

Two small modules and one property between them: **the chunks tile the recording, and every cut
that can be placed in a pause is.** The batch pass cut on a clock and reconciled the boundary by
word timestamp, and a real dictation showed what that costs — an ellipsis where a window had cut a
phrase in half, and three phrases transcribed twice. A cut inside a pause has no half-word on
either side, so there is nothing to reconcile.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.services.audio.formats import SAMPLE_RATE
from app.services.recording.chunks import MIN_TAIL_S, plan_chunks
from app.services.vad.base import VoiceActivityDetector
from app.services.vad.energy import EnergyVad
from app.services.vad.pauses import Pause, find_pauses


def tone(seconds: float, amplitude: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class Loudness(VoiceActivityDetector):
    """Speech is anything louder than a whisper. Deterministic, so a cut lands where a test says."""

    def __init__(self) -> None:
        self.resets = 0

    def is_speech(self, frame: np.ndarray) -> bool:
        return bool(np.sqrt(np.mean(np.square(frame, dtype=np.float64))) > 0.01)

    def set_sensitivity(self, sensitivity: float) -> None:
        pass

    def reset(self) -> None:
        self.resets += 1

    @property
    def name(self) -> str:
        return "loudness"


# -- finding the pauses ---------------------------------------------------------------------


class TestFindPauses:
    def test_a_pause_is_a_run_of_quiet_at_least_the_minimum_long(self) -> None:
        clip = np.concatenate([tone(1.0), silence(0.2), tone(1.0), silence(0.5), tone(1.0)])

        pauses = find_pauses(clip, Loudness(), min_pause_ms=300)

        assert len(pauses) == 1, "a 200 ms gap between words is not somewhere to cut"
        assert pauses[0].start_s == pytest.approx(2.2, abs=0.04)
        assert pauses[0].end_s == pytest.approx(2.7, abs=0.04)
        assert pauses[0].middle_s == pytest.approx(2.45, abs=0.04)

    def test_leading_and_trailing_silence_count(self) -> None:
        clip = np.concatenate([silence(0.5), tone(1.0), silence(0.5)])

        pauses = find_pauses(clip, Loudness(), min_pause_ms=300)

        assert [round(p.start_s, 1) for p in pauses] == [0.0, 1.5]

    def test_silence_throughout_is_one_pause(self) -> None:
        [pause] = find_pauses(silence(3.0), Loudness(), min_pause_ms=300)
        assert (pause.start_s, pause.end_s) == (0.0, 3.0)

    def test_speech_throughout_has_none(self) -> None:
        assert find_pauses(tone(3.0), Loudness(), min_pause_ms=300) == []

    def test_nothing_has_none(self) -> None:
        assert find_pauses(np.zeros(0, dtype=np.float32), Loudness()) == []

    def test_the_detector_is_reset_first(self) -> None:
        """An adaptive noise floor must learn *this* recording, not carry over the last one."""
        detector = Loudness()
        find_pauses(tone(0.5), detector)
        assert detector.resets == 1

    def test_the_energy_detector_finds_a_breath_in_synthetic_speech(self) -> None:
        """The real default detector over something shaped like speech: syllables at 4 Hz with a
        trough between them, a breath of room tone, more syllables. The breath is the only pause
        long enough to cut at; the syllable troughs are not."""
        rng = np.random.default_rng(0)

        def speech(seconds: float) -> np.ndarray:
            t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
            carrier = (
                0.45 * np.sin(2 * np.pi * 180 * t)
                + 0.28 * np.sin(2 * np.pi * 420 * t)
                + 0.14 * np.sin(2 * np.pi * 950 * t)
            )
            syllables = 0.12 + 0.88 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t))
            return (0.3 * carrier * syllables).astype(np.float32)

        def room(seconds: float) -> np.ndarray:
            return (rng.standard_normal(int(seconds * SAMPLE_RATE)) * 0.0015).astype(np.float32)

        clip = np.concatenate([room(0.3), speech(3.0), room(0.6), speech(3.0)])

        pauses = find_pauses(clip, EnergyVad(sensitivity=0.6), min_pause_ms=300)

        breaths = [p for p in pauses if p.start_s > 0.5]
        assert len(breaths) == 1, f"expected the one breath, found {pauses}"
        assert breaths[0].start_s == pytest.approx(3.3, abs=0.15)
        assert breaths[0].end_s == pytest.approx(3.9, abs=0.15)


# -- planning the chunks --------------------------------------------------------------------


def boundaries(chunks) -> list[float]:  # noqa: ANN001
    return [chunks[0].start_s, *(chunk.end_s for chunk in chunks)]


class TestPlanChunks:
    def test_a_short_recording_is_one_chunk(self) -> None:
        [chunk] = plan_chunks(tone(5.0), [Pause(2.0, 2.5)], max_chunk_s=10.0)

        assert (chunk.index, chunk.start_s, chunk.end_s) == (0, 0.0, 5.0)
        assert chunk.ends_at_pause is True
        assert chunk.samples.size == 5 * SAMPLE_RATE

    def test_nothing_plans_nothing(self) -> None:
        assert plan_chunks(np.zeros(0, dtype=np.float32), [], max_chunk_s=10.0) == []

    def test_the_cut_lands_in_the_longest_pause_of_the_back_half(self) -> None:
        """Not the *last* pause before the limit: a breath between two words is a pause too, and
        the longest gap in a long enough stretch is far more often a sentence boundary."""
        pauses = [Pause(2.0, 2.4), Pause(6.0, 6.4), Pause(8.0, 9.0), Pause(9.5, 9.8)]

        chunks = plan_chunks(silence(20.0), pauses, max_chunk_s=10.0)

        # The first chunk may reach 10 s; its back half is 5-10 s; the longest pause there is
        # 8.0-9.0 s. The second chunk starts at 8.5 s and finds no pause in *its* back half
        # (13.5-18.5 s), so it is cut hard at its limit, and says so.
        assert boundaries(chunks) == pytest.approx([0.0, 8.5, 18.5, 20.0])
        assert [chunk.ends_at_pause for chunk in chunks] == [True, False, True]

    def test_the_chunks_tile_the_recording_exactly(self) -> None:
        clip = np.arange(20 * SAMPLE_RATE, dtype=np.float32)
        pauses = [Pause(7.0, 7.5), Pause(15.0, 15.2)]

        chunks = plan_chunks(clip, pauses, max_chunk_s=10.0)

        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
        assert all(chunk.duration_s <= 10.0 + 1e-9 for chunk in chunks)
        np.testing.assert_array_equal(np.concatenate([c.samples for c in chunks]), clip)

    def test_a_pause_running_past_the_limit_is_cut_at_the_limit(self) -> None:
        """A long silence straddling the limit: the cut stays inside the pause *and* inside the
        chunk's allowance."""
        chunks = plan_chunks(silence(20.0), [Pause(9.8, 12.0)], max_chunk_s=10.0)

        assert chunks[0].end_s == pytest.approx(10.0)
        assert chunks[0].ends_at_pause is True

    def test_a_pause_beginning_before_the_back_half_is_cut_inside_it(self) -> None:
        chunks = plan_chunks(silence(20.0), [Pause(3.0, 5.4)], max_chunk_s=10.0)

        assert 5.0 <= chunks[0].end_s <= 5.4
        assert chunks[0].ends_at_pause is True

    def test_a_pause_entirely_in_the_front_half_is_not_used(self) -> None:
        """Cutting a ten-second allowance at three seconds would triple the number of passes for
        nothing; the front half is off limits and the chunk is cut hard instead."""
        chunks = plan_chunks(silence(20.0), [Pause(3.0, 3.5)], max_chunk_s=10.0)

        assert chunks[0].end_s == pytest.approx(10.0)
        assert chunks[0].ends_at_pause is False

    def test_a_sliver_after_the_last_cut_is_folded_into_the_chunk_before(self) -> None:
        """Half a second of trailing silence is not worth a pass, and handing a speech model
        nothing but silence is how it comes to say "you"."""
        total = 10.0 + MIN_TAIL_S / 2
        chunks = plan_chunks(silence(total), [Pause(9.6, 9.8)], max_chunk_s=10.0)

        assert len(chunks) == 1
        assert chunks[0].end_s == pytest.approx(total)

    def test_a_non_positive_limit_is_refused(self) -> None:
        with pytest.raises(ValueError):
            plan_chunks(silence(1.0), [], max_chunk_s=0.0)

    def test_pauses_arrive_in_any_order(self) -> None:
        shuffled = [Pause(8.0, 9.0), Pause(2.0, 2.4), Pause(6.0, 6.4)]
        ordered = sorted(shuffled, key=lambda p: p.start_s)

        assert boundaries(plan_chunks(silence(20.0), shuffled, max_chunk_s=10.0)) == boundaries(
            plan_chunks(silence(20.0), ordered, max_chunk_s=10.0)
        )
