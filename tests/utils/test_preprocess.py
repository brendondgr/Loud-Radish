"""Preprocessing and level metering (BE §4.1, §4.5).

Each preprocessing stage can hurt as easily as help, so each is tested for the specific harm it is
supposed to avoid: the filter must not attenuate speech, and normalisation must not amplify silence.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.config.schema import AudioConfig
from app.services.audio.formats import SAMPLE_RATE
from app.services.audio.level import CLIPPING_THRESHOLD, SILENCE_DBFS, LevelMeter, measure, to_dbfs
from app.services.audio.preprocess import (
    NOISE_FLOOR_RMS,
    GainNormaliser,
    HighPassFilter,
    PreprocessChain,
)


def tone(frequency: float, seconds: float = 0.5, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


class TestHighPassFilter:
    def test_rumble_well_below_the_cutoff_is_attenuated(self) -> None:
        signal = tone(20.0)
        filtered = HighPassFilter(cutoff_hz=80.0).process(signal)
        assert rms(filtered) < rms(signal) * 0.5

    def test_speech_band_content_passes_largely_intact(self) -> None:
        """A filter that eats the speech band costs more accuracy than the rumble did."""
        signal = tone(500.0)
        filtered = HighPassFilter(cutoff_hz=80.0).process(signal)
        assert rms(filtered) > rms(signal) * 0.9

    def test_state_carries_across_frames(self) -> None:
        """Resetting per frame would produce a click at every 32 ms boundary."""
        signal = tone(20.0, seconds=0.2)
        stateful = HighPassFilter()
        chunked = np.concatenate(
            [stateful.process(signal[i : i + 512]) for i in range(0, signal.size, 512)]
        )
        whole = HighPassFilter().process(signal)
        assert chunked == pytest.approx(whole, abs=1e-5)

    def test_reset_clears_state(self) -> None:
        filt = HighPassFilter()
        filt.process(tone(20.0))
        filt.reset()
        assert filt.process(np.zeros(100, dtype=np.float32)) == pytest.approx(np.zeros(100))

    def test_an_empty_frame_is_returned_unchanged(self) -> None:
        assert HighPassFilter().process(np.zeros(0, dtype=np.float32)).size == 0

    def test_a_cutoff_at_or_above_nyquist_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            HighPassFilter(cutoff_hz=SAMPLE_RATE)


class TestGainNormaliser:
    def test_quiet_input_is_brought_up_towards_the_target(self) -> None:
        normaliser = GainNormaliser()
        quiet = tone(300.0, seconds=0.05, amplitude=0.01)
        for _ in range(200):
            loudest = normaliser.process(quiet)
        assert rms(loudest) > rms(quiet) * 3

    def test_silence_is_not_amplified(self) -> None:
        """Amplifying room tone during pauses is exactly when the ASR hallucinates."""
        normaliser = GainNormaliser()
        near_silence = np.full(512, NOISE_FLOOR_RMS / 4, dtype=np.float32)
        for _ in range(200):
            out = normaliser.process(near_silence)
        assert normaliser.gain == pytest.approx(1.0)
        assert rms(out) == pytest.approx(rms(near_silence), abs=1e-6)

    def test_output_never_leaves_the_valid_range(self) -> None:
        normaliser = GainNormaliser()
        for _ in range(50):
            out = normaliser.process(tone(300.0, seconds=0.05, amplitude=0.001))
        assert float(np.max(np.abs(out))) <= 1.0

    def test_gain_moves_gradually_rather_than_jumping(self) -> None:
        """An instantaneous correction pumps audibly, and pumped audio transcribes worse."""
        normaliser = GainNormaliser()
        normaliser.process(tone(300.0, seconds=0.05, amplitude=0.01))
        assert normaliser.gain < 3.0

    def test_reset_returns_to_unity(self) -> None:
        normaliser = GainNormaliser()
        normaliser.process(tone(300.0, seconds=0.05, amplitude=0.01))
        normaliser.reset()
        assert normaliser.gain == pytest.approx(1.0)


class TestPreprocessChain:
    def test_all_stages_disabled_is_a_pass_through(self) -> None:
        config = AudioConfig(high_pass=False, normalise_gain=False, gain_db=0.0)
        signal = tone(300.0, seconds=0.05)
        assert PreprocessChain(config).process(signal) == pytest.approx(signal)

    def test_static_gain_is_applied_in_decibels(self) -> None:
        config = AudioConfig(high_pass=False, normalise_gain=False, gain_db=6.0)
        signal = tone(300.0, seconds=0.05, amplitude=0.1)
        assert rms(PreprocessChain(config).process(signal)) == pytest.approx(
            rms(signal) * 2.0, rel=0.02
        )

    def test_the_filter_runs_before_normalisation(self) -> None:
        """Reversed, low-frequency noise would inflate the measured RMS and suppress speech gain."""
        config = AudioConfig(high_pass=True, normalise_gain=True)
        chain = PreprocessChain(config)
        rumble_plus_speech = tone(20.0, seconds=0.05, amplitude=0.6) + tone(
            500.0, seconds=0.05, amplitude=0.05
        )
        for _ in range(60):
            out = chain.process(rumble_plus_speech)
        assert rms(out) > rms(tone(500.0, seconds=0.05, amplitude=0.05))

    def test_updating_live_settings_keeps_filter_state(self) -> None:
        chain = PreprocessChain(AudioConfig(high_pass=True, gain_db=0.0))
        chain.process(tone(300.0, seconds=0.05))
        chain.update_config(AudioConfig(high_pass=True, gain_db=3.0))
        assert chain.process(tone(300.0, seconds=0.05)).size == int(0.05 * SAMPLE_RATE)

    def test_an_empty_frame_is_returned_unchanged(self) -> None:
        chain = PreprocessChain(AudioConfig())
        assert chain.process(np.zeros(0, dtype=np.float32)).size == 0


class TestLevelMetering:
    def test_silence_reads_as_zero(self) -> None:
        level = measure(np.zeros(512, dtype=np.float32))
        assert level.rms == 0.0
        assert level.peak == 0.0
        assert not level.clipping

    def test_an_empty_frame_reports_silence_rather_than_raising(self) -> None:
        """The meter is polled on a timer and can arrive between frames."""
        assert measure(np.zeros(0, dtype=np.float32)).rms == 0.0

    def test_peak_and_rms_are_measured_separately(self) -> None:
        signal = tone(300.0, seconds=0.05, amplitude=0.5)
        level = measure(signal)
        assert level.peak == pytest.approx(0.5, abs=0.01)
        assert level.rms == pytest.approx(0.5 / np.sqrt(2), abs=0.01)

    def test_clipping_is_flagged_at_full_scale(self) -> None:
        assert measure(np.full(64, CLIPPING_THRESHOLD, dtype=np.float32)).clipping

    def test_dbfs_is_floored_rather_than_negative_infinity(self) -> None:
        assert to_dbfs(0.0) == SILENCE_DBFS
        assert to_dbfs(1.0) == pytest.approx(0.0)
        assert to_dbfs(0.5) == pytest.approx(-6.02, abs=0.01)

    def test_the_event_payload_is_json_safe(self) -> None:
        payload = measure(tone(300.0, seconds=0.05)).as_event()
        assert set(payload) == {"rms", "peak", "clipping", "rms_dbfs"}
        assert isinstance(payload["clipping"], bool)

    def test_the_meter_rises_quickly_and_falls_slowly(self) -> None:
        """Fast attack makes speech onset visible; slow release stops flicker between syllables."""
        meter = LevelMeter()
        loud = tone(300.0, seconds=0.05, amplitude=0.8)
        silent = np.zeros(512, dtype=np.float32)

        risen = meter.update(loud).rms
        assert risen > 0.2

        fallen = meter.update(silent).rms
        assert fallen > risen * 0.5

    def test_reset_returns_the_meter_to_silence(self) -> None:
        meter = LevelMeter()
        meter.update(tone(300.0, seconds=0.05, amplitude=0.8))
        meter.reset()
        assert meter.update(np.zeros(0, dtype=np.float32)).rms == 0.0

    def test_invalid_smoothing_coefficients_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            LevelMeter(attack=0.0)
