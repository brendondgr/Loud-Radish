"""Hysteresis and pause detection (BE §5.2, §5.3).

Tested against a scripted detector rather than a real one, so the debouncing rule is verified
independently of whether any particular detector classified a frame correctly.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.config.schema import VadConfig
from app.services.vad import SpeechGate, SpeechState, build_gate
from app.services.vad.base import VoiceActivityDetector

FRAME = np.zeros(512, dtype=np.float32)
FRAME_MS = 32


class ScriptedVad(VoiceActivityDetector):
    """Returns a predetermined sequence of answers, so the gate can be tested in isolation."""

    def __init__(self, answers: list[bool]) -> None:
        self.answers = list(answers)
        self.index = 0
        self.sensitivity = 0.6
        self.resets = 0

    def is_speech(self, frame: np.ndarray) -> bool:
        if self.index < len(self.answers):
            answer = self.answers[self.index]
        else:
            answer = self.answers[-1] if self.answers else False
        self.index += 1
        return answer

    def set_sensitivity(self, sensitivity: float) -> None:
        self.sensitivity = sensitivity

    def reset(self) -> None:
        self.resets += 1
        self.index = 0

    @property
    def name(self) -> str:
        return "scripted"


def gate(answers: list[bool], **overrides: object) -> SpeechGate:
    settings: dict = {"enter_frames": 3, "leave_frames": 10, "pause_ms": 500, **overrides}
    return SpeechGate(ScriptedVad(answers), VadConfig(**settings), frame_ms=FRAME_MS)


def run(speech_gate: SpeechGate, frames: int) -> list:
    return [speech_gate.process(FRAME) for _ in range(frames)]


class TestEnteringSpeech:
    def test_entering_requires_a_run_of_speech_frames(self) -> None:
        """One noisy frame is a cough, not a speaker."""
        speech_gate = gate([True, True, True, True])
        results = run(speech_gate, 4)

        assert [r.state for r in results[:2]] == [SpeechState.SILENT, SpeechState.SILENT]
        assert results[2].state is SpeechState.SPEAKING
        assert results[2].state_changed

    def test_an_isolated_speech_frame_does_not_enter(self) -> None:
        speech_gate = gate([False, True, False, False, False])
        assert all(not r.speaking for r in run(speech_gate, 5))

    def test_the_run_must_be_consecutive(self) -> None:
        """True, False, True, True is two frames of run, not three."""
        speech_gate = gate([True, False, True, True, False])
        assert all(not r.speaking for r in run(speech_gate, 5))

    def test_state_changed_fires_only_on_the_transition(self) -> None:
        """The caller emits ``vad.state`` on change; firing every frame would flood the socket."""
        speech_gate = gate([True] * 10)
        results = run(speech_gate, 10)
        assert sum(1 for r in results if r.state_changed) == 1


class TestLeavingSpeech:
    def test_leaving_requires_a_longer_run_than_entering(self) -> None:
        """Leaving early cuts the speaker off mid-sentence; entering late costs a moment."""
        speech_gate = gate([True] * 5 + [False] * 12)
        results = run(speech_gate, 17)

        # Speech begins at frame 3; ten silent frames are needed to leave, so frames 5..13 stay
        # in the speaking state.
        assert results[12].speaking
        assert not results[14].speaking

    def test_a_brief_gap_between_words_does_not_leave(self) -> None:
        speech_gate = gate([True] * 5 + [False] * 4 + [True] * 5)
        results = run(speech_gate, 14)
        assert all(r.speaking for r in results[3:])

    def test_flickering_input_does_not_toggle_the_state(self) -> None:
        """Without hysteresis the state flickers on every breath."""
        speech_gate = gate([True, False] * 20)
        results = run(speech_gate, 40)
        assert sum(1 for r in results if r.state_changed) == 0


class TestPauseEvents:
    def test_a_pause_fires_once_the_silence_threshold_is_crossed(self) -> None:
        """The engine treats these as preferred commit boundaries — a place a word is not sliced."""
        speech_gate = gate([True] * 5 + [False] * 40)
        results = run(speech_gate, 45)

        pauses = [i for i, r in enumerate(results) if r.pause_event]
        assert len(pauses) == 1
        # 500 ms of silence at 32 ms per frame is 16 frames after speech ends at index 4.
        assert pauses[0] == pytest.approx(4 + 16, abs=1)

    def test_a_pause_fires_only_once_per_silence_run(self) -> None:
        speech_gate = gate([True] * 5 + [False] * 200)
        assert sum(1 for r in run(speech_gate, 205) if r.pause_event) == 1

    def test_a_new_silence_run_can_fire_again(self) -> None:
        script = [True] * 5 + [False] * 40 + [True] * 5 + [False] * 40
        assert sum(1 for r in run(gate(script), len(script)) if r.pause_event) == 2

    def test_a_short_gap_does_not_fire(self) -> None:
        speech_gate = gate([True] * 5 + [False] * 10 + [True] * 5)
        assert not any(r.pause_event for r in run(speech_gate, 20))

    def test_continuous_speech_never_produces_a_pause(self) -> None:
        """BE §5.3: the forced-commit timeout, not the VAD, is the safety net here."""
        speech_gate = gate([True] * 4000)
        results = run(speech_gate, 4000)
        assert not any(r.pause_event for r in results)
        assert results[-1].speaking

    def test_the_pause_threshold_is_configurable(self) -> None:
        """A noisy hall needs a different threshold from a quiet one."""
        slow = gate([True] * 5 + [False] * 200, pause_ms=2000)
        pauses = [i for i, r in enumerate(run(slow, 205)) if r.pause_event]
        assert pauses[0] == pytest.approx(4 + 63, abs=2)


class TestDisabledVad:
    def test_disabling_the_vad_treats_everything_as_speech(self) -> None:
        """Gating everything out would look exactly like a broken microphone."""
        speech_gate = gate([False] * 20, enabled=False)
        results = run(speech_gate, 20)
        assert all(r.speaking for r in results)
        assert not any(r.pause_event for r in results)

    def test_the_first_frame_reports_the_state_change(self) -> None:
        assert gate([False], enabled=False).process(FRAME).state_changed


class TestGateLifecycle:
    def test_silence_seconds_tracks_the_current_run(self) -> None:
        speech_gate = gate([True] * 5 + [False] * 10)
        results = run(speech_gate, 15)
        assert results[-1].silence_seconds == pytest.approx(10 * FRAME_MS / 1000.0)

    def test_speech_seconds_accumulates_across_the_session(self) -> None:
        speech_gate = gate([True] * 20)
        run(speech_gate, 20)
        assert speech_gate.speech_seconds == pytest.approx(20 * FRAME_MS / 1000.0)

    def test_updating_config_keeps_the_speech_state(self) -> None:
        """Nudging a slider mid-talk must not drop the speaker mid-sentence."""
        speech_gate = gate([True] * 10)
        run(speech_gate, 5)
        assert speech_gate.speaking

        speech_gate.update_config(VadConfig(sensitivity=0.9, enter_frames=3, leave_frames=10))
        assert speech_gate.speaking

    def test_updating_config_passes_sensitivity_to_the_detector(self) -> None:
        detector = ScriptedVad([True])
        speech_gate = SpeechGate(detector, VadConfig(), frame_ms=FRAME_MS)
        speech_gate.update_config(VadConfig(sensitivity=0.2))
        assert detector.sensitivity == pytest.approx(0.2)

    def test_reset_returns_to_silence_and_resets_the_detector(self) -> None:
        detector = ScriptedVad([True] * 10)
        speech_gate = SpeechGate(detector, VadConfig(enter_frames=3), frame_ms=FRAME_MS)
        run(speech_gate, 5)
        speech_gate.reset()

        assert speech_gate.state is SpeechState.SILENT
        assert speech_gate.speech_seconds == 0.0
        assert detector.resets == 1

    def test_the_gate_reports_which_detector_is_behind_it(self) -> None:
        assert gate([True]).detector_name == "scripted"


class TestFactory:
    def test_build_gate_defaults_to_the_energy_detector(self) -> None:
        assert build_gate(VadConfig()).detector_name == "energy"

    def test_silero_falls_back_to_energy_when_unavailable(self) -> None:
        """A missing optional dependency degrades the detector; it must not stop the session."""
        assert build_gate(VadConfig(detector="silero")).detector_name == "energy"
