"""The canonical audio format and conversion into it (BE §4.2)."""

from __future__ import annotations

import numpy as np
import pytest
from app.services.audio.formats import (
    MAX_FRAME_MS,
    SAMPLE_RATE,
    AudioFormatError,
    clip_to_range,
    downmix,
    duration_seconds,
    frame_samples,
    is_canonical,
    to_canonical,
    to_float32,
)
from app.services.audio.resample import resample, resampler_name


class TestFrameSizing:
    def test_frame_samples_matches_the_duration(self) -> None:
        assert frame_samples(32) == 512
        assert frame_samples(100) == 1600

    def test_a_frame_shorter_than_the_minimum_is_rejected(self) -> None:
        with pytest.raises(AudioFormatError):
            frame_samples(5)

    def test_a_frame_longer_than_the_maximum_is_rejected(self) -> None:
        with pytest.raises(AudioFormatError):
            frame_samples(MAX_FRAME_MS + 1)

    def test_duration_seconds_inverts_frame_samples(self) -> None:
        assert duration_seconds(frame_samples(50)) == pytest.approx(0.05)


class TestDtypeConversion:
    def test_int16_scales_to_the_full_range(self) -> None:
        samples = np.array([-32768, 0, 32767], dtype=np.int16)
        converted = to_float32(samples)
        assert converted.dtype == np.float32
        assert converted[0] == pytest.approx(-1.0)
        assert converted[1] == pytest.approx(0.0)
        assert converted[2] == pytest.approx(1.0, abs=1e-4)

    def test_int32_scales_without_overflowing_the_range(self) -> None:
        samples = np.array([np.iinfo(np.int32).min, np.iinfo(np.int32).max], dtype=np.int32)
        converted = to_float32(samples)
        assert np.all(np.abs(converted) <= 1.0)

    def test_unsigned_int8_is_centred_on_zero(self) -> None:
        """8-bit WAV is unsigned with midpoint 128; not centring it adds a DC offset."""
        samples = np.array([0, 128, 255], dtype=np.uint8)
        converted = to_float32(samples)
        assert converted[0] == pytest.approx(-1.0)
        assert converted[1] == pytest.approx(0.0)
        assert converted[2] == pytest.approx(1.0, abs=0.01)

    def test_float32_passes_through_unchanged(self) -> None:
        samples = np.array([0.5, -0.25], dtype=np.float32)
        assert to_float32(samples) is samples

    def test_float64_narrows_to_float32(self) -> None:
        assert to_float32(np.array([0.5], dtype=np.float64)).dtype == np.float32

    def test_an_unsupported_dtype_is_rejected(self) -> None:
        with pytest.raises(AudioFormatError):
            to_float32(np.array(["a"], dtype="<U1"))


class TestDownmix:
    def test_stereo_averages_to_mono(self) -> None:
        stereo = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]], dtype=np.float32)
        assert downmix(stereo) == pytest.approx([0.5, 0.5, 0.5])

    def test_mono_passes_through(self) -> None:
        mono = np.array([0.1, 0.2], dtype=np.float32)
        assert downmix(mono) is mono

    def test_a_single_channel_column_is_flattened(self) -> None:
        assert downmix(np.array([[0.3], [0.4]], dtype=np.float32)).shape == (2,)

    def test_three_dimensional_input_is_rejected(self) -> None:
        with pytest.raises(AudioFormatError):
            downmix(np.zeros((2, 2, 2), dtype=np.float32))


class TestResampling:
    def test_matching_rates_are_a_no_op(self) -> None:
        samples = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        assert resample(samples, SAMPLE_RATE, SAMPLE_RATE) is not None
        assert np.array_equal(resample(samples, SAMPLE_RATE, SAMPLE_RATE), samples)

    def test_downsampling_shortens_by_the_rate_ratio(self) -> None:
        one_second = np.zeros(48_000, dtype=np.float32)
        converted = resample(one_second, 48_000, 16_000)
        assert converted.size == pytest.approx(16_000, rel=0.01)

    def test_upsampling_lengthens_by_the_rate_ratio(self) -> None:
        converted = resample(np.zeros(8_000, dtype=np.float32), 8_000, 16_000)
        assert converted.size == pytest.approx(16_000, rel=0.01)

    def test_a_tone_survives_downsampling_at_roughly_the_same_level(self) -> None:
        """Naive decimation aliases; a proper resampler preserves an in-band tone's amplitude."""
        t = np.arange(48_000, dtype=np.float64) / 48_000
        tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        converted = resample(tone, 48_000, 16_000)
        assert float(np.max(np.abs(converted))) == pytest.approx(0.5, abs=0.05)

    def test_empty_input_returns_empty(self) -> None:
        assert resample(np.zeros(0, dtype=np.float32), 48_000, 16_000).size == 0

    def test_a_non_positive_rate_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            resample(np.zeros(10, dtype=np.float32), 0, 16_000)

    def test_the_proper_resampler_is_in_use(self) -> None:
        """The linear fallback measurably hurts recognition; it must not be the default path."""
        assert resampler_name() == "soxr"


class TestToCanonical:
    def test_stereo_int16_at_44100_becomes_mono_float32_at_16000(self) -> None:
        stereo = np.zeros((44_100, 2), dtype=np.int16)
        converted = to_canonical(stereo, 44_100)
        assert is_canonical(converted)
        assert converted.size == pytest.approx(16_000, rel=0.01)

    def test_an_interleaved_buffer_is_deinterleaved_when_channels_are_given(self) -> None:
        interleaved = np.array([1.0, -1.0, 1.0, -1.0], dtype=np.float32)
        assert to_canonical(interleaved, SAMPLE_RATE, channels=2) == pytest.approx([0.0, 0.0])

    def test_an_interleaved_buffer_of_the_wrong_length_is_rejected(self) -> None:
        with pytest.raises(AudioFormatError):
            to_canonical(np.zeros(5, dtype=np.float32), SAMPLE_RATE, channels=2)

    def test_the_result_is_contiguous(self) -> None:
        """Model backends assume a contiguous buffer; a strided view silently corrupts input."""
        converted = to_canonical(np.zeros((1000, 2), dtype=np.int16), SAMPLE_RATE)
        assert converted.flags["C_CONTIGUOUS"]


class TestClipping:
    def test_values_are_clamped_into_range(self) -> None:
        clipped = clip_to_range(np.array([-3.0, -0.5, 0.5, 3.0], dtype=np.float32))
        assert clipped == pytest.approx([-1.0, -0.5, 0.5, 1.0])

    def test_the_dtype_stays_float32(self) -> None:
        assert clip_to_range(np.array([2.0], dtype=np.float32)).dtype == np.float32
