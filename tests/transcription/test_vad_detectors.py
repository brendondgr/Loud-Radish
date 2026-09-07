"""The energy detector, and the Silero adapter's availability behaviour."""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest
from app.config.schema import VadConfig
from app.services.audio.formats import SAMPLE_RATE
from app.services.vad import EnergyVad, build_detector
from app.services.vad.energy import ABSOLUTE_FLOOR_RMS
from app.services.vad.silero import SileroUnavailableError, SileroVad

FRAME_SAMPLES = 512


def speech_frame(amplitude: float = 0.15, frequency: float = 240.0) -> np.ndarray:
    """A frame with a speech-like fundamental and enough harmonic content to cross zero often."""
    t = np.arange(FRAME_SAMPLES, dtype=np.float64) / SAMPLE_RATE
    signal = (
        np.sin(2 * np.pi * frequency * t)
        + 0.5 * np.sin(2 * np.pi * frequency * 2.5 * t)
        + 0.3 * np.sin(2 * np.pi * frequency * 4.1 * t)
    )
    return (amplitude * signal / np.max(np.abs(signal))).astype(np.float32)


def silent_frame() -> np.ndarray:
    return np.zeros(FRAME_SAMPLES, dtype=np.float32)


def room_tone(amplitude: float = 0.0015, seed: int = 0) -> np.ndarray:
    """Low-level broadband noise — a quiet but not silent room."""
    rng = np.random.default_rng(seed)
    return (amplitude * rng.standard_normal(FRAME_SAMPLES)).astype(np.float32)


def settle(detector: EnergyVad, frames: int = 40, amplitude: float = 0.0015) -> EnergyVad:
    """Let the adaptive noise floor learn the room before asserting anything about speech."""
    for i in range(frames):
        detector.is_speech(room_tone(amplitude, seed=i))
    return detector


class TestEnergyVad:
    def test_digital_silence_is_not_speech(self) -> None:
        detector = EnergyVad()
        assert not detector.is_speech(silent_frame())

    def test_an_empty_frame_is_not_speech(self) -> None:
        assert not EnergyVad().is_speech(np.zeros(0, dtype=np.float32))

    def test_a_speech_like_frame_above_the_floor_is_speech(self) -> None:
        assert settle(EnergyVad()).is_speech(speech_frame())

    def test_room_tone_alone_is_not_speech(self) -> None:
        """Otherwise every pause becomes a hallucination opportunity."""
        assert not settle(EnergyVad(), frames=60).is_speech(room_tone(seed=999))

    def test_a_loud_low_frequency_hum_is_rejected(self) -> None:
        """Ventilation rumble can be as loud as speech and has nothing like its structure."""
        t = np.arange(FRAME_SAMPLES, dtype=np.float64) / SAMPLE_RATE
        hum = (0.3 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
        assert not settle(EnergyVad()).is_speech(hum)

    def test_continuous_syllable_modulated_speech_is_detected_throughout(self) -> None:
        """The calibration regression test.

        Real speech never stops varying, and the noise floor settles near the *trough between
        syllables* rather than at true silence. A margin chosen from peak-versus-silence intuition
        is several times too large and rejects continuous speech outright — which presents as the
        transcript simply stopping, with nothing in the UI to explain why. Locked in here so the
        margin cannot be quietly tightened back.
        """
        detector = EnergyVad(sensitivity=0.6)
        seconds = 12.0
        t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
        tone = (
            0.45 * np.sin(2 * np.pi * 180 * t)
            + 0.28 * np.sin(2 * np.pi * 420 * t)
            + 0.14 * np.sin(2 * np.pi * 950 * t)
        )
        syllables = 0.12 + 0.88 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t))
        signal = (tone * syllables * 0.5).astype(np.float32)

        frames = [
            signal[i : i + FRAME_SAMPLES]
            for i in range(0, signal.size - FRAME_SAMPLES, FRAME_SAMPLES)
        ]
        detected = [detector.is_speech(frame) for frame in frames]

        # Roughly half of a syllable cycle sits above the floor; anything near zero means the
        # detector has gated out a talking speaker.
        assert np.mean(detected) > 0.3, (
            f"only {np.mean(detected):.0%} of continuous speech detected"
        )

    def test_a_constant_level_tone_is_still_rejected(self) -> None:
        """The other side of that calibration: a fan or a hum is not speech, however loud."""
        detector = EnergyVad(sensitivity=0.6)
        t = np.arange(int(12.0 * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
        constant = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)

        frames = [
            constant[i : i + FRAME_SAMPLES]
            for i in range(0, constant.size - FRAME_SAMPLES, FRAME_SAMPLES)
        ]
        detected = [detector.is_speech(frame) for frame in frames]
        assert np.mean(detected[20:]) < 0.05

    def test_the_noise_floor_adapts_to_a_louder_room(self) -> None:
        quiet = EnergyVad()
        loud = EnergyVad()
        for i in range(200):
            quiet.is_speech(room_tone(0.0005, seed=i))
            loud.is_speech(room_tone(0.02, seed=i))
        assert loud.noise_floor > quiet.noise_floor

    def test_the_floor_never_falls_below_the_absolute_minimum(self) -> None:
        """Otherwise a silent room divides by an ever-smaller number and all reads as speech."""
        detector = EnergyVad()
        for _ in range(500):
            detector.is_speech(silent_frame())
        assert detector.noise_floor >= ABSOLUTE_FLOOR_RMS * 0.5
        assert not detector.is_speech(room_tone(0.0002))

    def test_higher_sensitivity_detects_quieter_speech(self) -> None:
        insensitive = settle(EnergyVad(sensitivity=0.0))
        sensitive = settle(EnergyVad(sensitivity=1.0))
        quiet = speech_frame(amplitude=0.01)
        assert sensitive.is_speech(quiet)
        assert not insensitive.is_speech(quiet)

    def test_sensitivity_outside_the_range_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            EnergyVad().set_sensitivity(1.5)

    def test_reset_forgets_the_learned_floor(self) -> None:
        detector = EnergyVad()
        for i in range(200):
            detector.is_speech(room_tone(0.05, seed=i))
        assert detector.noise_floor > ABSOLUTE_FLOOR_RMS

        detector.reset()
        assert detector.noise_floor == ABSOLUTE_FLOOR_RMS

    def test_score_rises_with_level(self) -> None:
        detector = settle(EnergyVad())
        assert detector.score(speech_frame(0.3)) >= detector.score(speech_frame(0.01))

    def test_score_of_an_empty_frame_is_zero(self) -> None:
        assert EnergyVad().score(np.zeros(0, dtype=np.float32)) == 0.0

    def test_it_reports_its_name(self) -> None:
        assert EnergyVad().name == "energy"


def _onnxruntime_installed() -> bool:
    return importlib.util.find_spec("onnxruntime") is not None


class TestSileroAvailability:
    """When Silero can and cannot be built, and what is said about it (D-052).

    **These tests used to assert the fault.** `test_the_factory_falls_back_rather_than_raising`
    passed for months for the wrong reason: `silero_vad.onnx` was on nobody's machine, so the
    factory always fell back, and a test asserting the fallback was really asserting that the
    feature did not work. Silero is now built from the model `faster-whisper` already carries, so
    the fallback has to be provoked to be tested at all.
    """

    def test_a_missing_dependency_names_what_is_needed_and_the_alternative(self) -> None:
        if _onnxruntime_installed():
            pytest.skip("onnxruntime is installed; this branch cannot be exercised")

        with pytest.raises(SileroUnavailableError) as excinfo:
            SileroVad(model_path="nonexistent.onnx")

        message = str(excinfo.value)
        assert "onnxruntime" in message
        assert "energy" in message
        assert "vad-silero" not in message, "naming a dependency group D-023 removed"

    def test_a_configured_path_that_is_missing_falls_back_to_the_bundled_model(self) -> None:
        """**Not an error.** A path that does not exist is a setting someone typed wrong, and
        refusing to run Silero over it — when a perfectly good copy is installed — is how this
        feature came to be switched off everywhere."""
        if not _onnxruntime_installed():
            pytest.skip("onnxruntime is absent; the dependency branch fires first")

        detector = SileroVad(model_path="nonexistent.onnx")

        assert detector.name == "silero"

    def test_with_no_model_anywhere_the_message_names_the_setting_to_change(
        self, monkeypatch
    ) -> None:
        if not _onnxruntime_installed():
            pytest.skip("onnxruntime is absent; the dependency branch fires first")
        from app.services.vad import silero as module

        monkeypatch.setattr(module, "bundled_model", lambda: None)

        with pytest.raises(SileroUnavailableError) as excinfo:
            SileroVad(model_path="nonexistent.onnx")

        message = str(excinfo.value)
        assert "vad.model_path" in message
        assert "energy" in message

    def test_the_factory_falls_back_rather_than_raising(self, monkeypatch) -> None:
        from app.services.vad import silero as module

        monkeypatch.setattr(module, "bundled_model", lambda: None)

        detector = build_detector(VadConfig(detector="silero", model_path="nope.onnx"))

        assert detector.name == "energy"

    def test_the_fallback_is_announced_and_not_only_logged(self, monkeypatch) -> None:
        """**The fault underneath the fault.** A `logger.warning` and nothing else meant the
        application reported "silero" in its status and ran the energy detector for months."""
        from app.services.vad import silero as module

        monkeypatch.setattr(module, "bundled_model", lambda: None)
        told: list[str] = []

        build_detector(VadConfig(detector="silero", model_path="nope.onnx"), told.append)

        assert told, "the fallback happened silently"
        assert "energy" in told[0].lower() or "silero" in told[0].lower()

    def test_silero_is_built_when_a_model_is_available(self) -> None:
        """The state this repository should have been in all along."""
        if not _onnxruntime_installed():
            pytest.skip("onnxruntime is absent")

        assert build_detector(VadConfig(detector="silero")).name == "silero"

    def test_the_factory_passes_sensitivity_through(self) -> None:
        """Named explicitly rather than relying on the default, which is now Silero (D-052) — a
        test about sensitivity should not also be a test about which detector ships."""
        detector = build_detector(VadConfig(detector="energy", sensitivity=0.9))

        assert isinstance(detector, EnergyVad)
        assert settle(detector).is_speech(speech_frame(amplitude=0.02))


class TestSileroInference:
    """Silero's windowing logic, exercised against a stub session.

    The real model is not installed here, but the buffering that turns arbitrary frame sizes into
    the fixed 512-sample windows the model requires is ours, and is worth testing.
    """

    def test_frames_are_buffered_into_whole_windows(self) -> None:
        session = _StubSession(scores=[0.9])
        detector = SileroVad(model_path=__file__, sensitivity=0.6, session=session)

        detector.score(np.zeros(300, dtype=np.float32))
        assert session.calls == 0, "a partial window must not be sent to the model"

        detector.score(np.zeros(300, dtype=np.float32))
        assert session.calls == 1

    def test_a_large_frame_produces_several_windows(self) -> None:
        session = _StubSession(scores=[0.1, 0.2, 0.95])
        detector = SileroVad(model_path=__file__, session=session)

        assert detector.score(np.zeros(512 * 3, dtype=np.float32)) == pytest.approx(0.95)
        assert session.calls == 3

    def test_the_highest_window_score_wins(self) -> None:
        """A frame straddling silence and speech onset reads as speech: the gate enters on time."""
        session = _StubSession(scores=[0.0, 0.99])
        detector = SileroVad(model_path=__file__, session=session)
        assert detector.is_speech(np.zeros(1024, dtype=np.float32))

    def test_reset_clears_buffered_audio(self) -> None:
        session = _StubSession(scores=[0.9])
        detector = SileroVad(model_path=__file__, session=session)

        detector.score(np.zeros(400, dtype=np.float32))
        detector.reset()
        detector.score(np.zeros(200, dtype=np.float32))
        assert session.calls == 0

    def test_sensitivity_moves_the_probability_threshold(self) -> None:
        session = _StubSession(scores=[0.4] * 10)
        detector = SileroVad(model_path=__file__, session=session)

        detector.set_sensitivity(1.0)
        assert detector.is_speech(np.zeros(512, dtype=np.float32))

        detector.set_sensitivity(0.0)
        assert not detector.is_speech(np.zeros(512, dtype=np.float32))

    def test_it_reports_its_name(self) -> None:
        assert SileroVad(model_path=__file__, session=_StubSession([0.0])).name == "silero"


class _StubSession:
    """Stands in for an ONNX inference session, returning scripted probabilities."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls = 0

    def run(self, _outputs, inputs):  # noqa: ANN001, ANN202 - mirrors the onnxruntime signature
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        return np.array([[score]], dtype=np.float32), inputs["state"]
