"""Speech-state hysteresis and pause detection (BE §5.2).

This sits above the detector interface so every detector shares one implementation of the part that
is easy to get subtly wrong.

**Hysteresis.** A raw per-frame boolean flickers on every breath and every consonant gap. Entering
the speaking state requires a run of consecutive speech frames; leaving it requires a longer run of
silent ones. The asymmetry is deliberate: the cost of entering late is a fraction of a second, and
the cost of leaving early is cutting a speaker off mid-sentence.

**Pause events.** When a silence run exceeds the configured threshold, a pause event fires. The
streaming engine treats these as preferred commit boundaries, because a pause is where cutting the
audio will not slice a word in half.

**The caution from BE §5.3.** The VAD must not be the only gate on transcription. A speaker who
talks for two minutes without a qualifying pause must not produce two minutes of silence — that
is what the streaming engine's forced-commit timeout is for. Nothing here should be relied on to
produce a commit boundary on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from ...config.schema import VadConfig
from ..audio.formats import SAMPLE_RATE
from .base import VoiceActivityDetector


class SpeechState(StrEnum):
    """The debounced speaking state, as reported in the ``vad.state`` event."""

    SILENT = "silent"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class VadFrameResult:
    """What one frame produced after debouncing."""

    #: The detector's raw, undebounced answer.
    raw_speech: bool
    #: The debounced state after this frame.
    state: SpeechState
    #: True on the frame where the state changed, so callers can emit ``vad.state`` only on change.
    state_changed: bool
    #: Set when a silence run just crossed the pause threshold. A preferred commit boundary.
    pause_event: bool
    #: Length of the current silence run, in seconds. Zero while speaking.
    silence_seconds: float

    @property
    def speaking(self) -> bool:
        """Whether the debounced state is speaking."""
        return self.state is SpeechState.SPEAKING


class SpeechGate:
    """Applies hysteresis and pause detection to a detector's per-frame answers."""

    def __init__(
        self,
        detector: VoiceActivityDetector,
        config: VadConfig,
        frame_ms: int = 32,
        sample_rate: int = SAMPLE_RATE,
    ) -> None:
        self._detector = detector
        self._config = config
        self._frame_seconds = frame_ms / 1000.0
        self._sample_rate = sample_rate

        self._state = SpeechState.SILENT
        self._speech_run = 0
        self._silence_run = 0
        self._pause_fired = False
        self._total_speech_seconds = 0.0
        self._total_silence_seconds = 0.0

    # -- state ---------------------------------------------------------------------

    @property
    def state(self) -> SpeechState:
        """The current debounced state."""
        return self._state

    @property
    def speaking(self) -> bool:
        """Whether the gate currently believes someone is speaking."""
        return self._state is SpeechState.SPEAKING

    @property
    def detector_name(self) -> str:
        """Which detector is behind this gate."""
        return self._detector.name

    @property
    def silence_seconds(self) -> float:
        """How long the current silence run has lasted. Zero while speaking."""
        return self._silence_run * self._frame_seconds

    @property
    def speech_seconds(self) -> float:
        """Total speech duration observed, for the session's speech/silence ratio."""
        return self._total_speech_seconds

    # -- processing ----------------------------------------------------------------

    def process(self, frame: np.ndarray) -> VadFrameResult:
        """Classify one frame and update the debounced state."""
        if not self._config.enabled:
            # Disabled means "everything is speech" rather than "nothing is": with the VAD off,
            # the pipeline must still transcribe. Silently gating everything out would look like a
            # broken microphone.
            return VadFrameResult(
                raw_speech=True,
                state=SpeechState.SPEAKING,
                state_changed=self._flip_to(SpeechState.SPEAKING),
                pause_event=False,
                silence_seconds=0.0,
            )

        raw = self._detector.is_speech(frame)
        if raw:
            self._speech_run += 1
            self._silence_run = 0
            self._total_speech_seconds += self._frame_seconds
        else:
            self._silence_run += 1
            self._speech_run = 0
            self._total_silence_seconds += self._frame_seconds

        changed = self._advance(raw)
        pause = self._check_pause()

        return VadFrameResult(
            raw_speech=raw,
            state=self._state,
            state_changed=changed,
            pause_event=pause,
            silence_seconds=self.silence_seconds,
        )

    def _advance(self, raw: bool) -> bool:
        """Apply the hysteresis rule. Returns whether the state changed on this frame."""
        if self._state is SpeechState.SILENT:
            if raw and self._speech_run >= self._config.enter_frames:
                self._state = SpeechState.SPEAKING
                self._pause_fired = False
                return True
        elif not raw and self._silence_run >= self._config.leave_frames:
            self._state = SpeechState.SILENT
            return True
        return False

    def _check_pause(self) -> bool:
        """Fire once per silence run, when it first exceeds the pause threshold."""
        if self._pause_fired or self._silence_run == 0:
            return False
        if self.silence_seconds * 1000.0 < self._config.pause_ms:
            return False
        self._pause_fired = True
        return True

    def _flip_to(self, state: SpeechState) -> bool:
        """Force a state, reporting whether it changed. Used when the VAD is disabled."""
        if self._state is state:
            return False
        self._state = state
        return True

    # -- lifecycle -----------------------------------------------------------------

    def update_config(self, config: VadConfig) -> None:
        """Adopt new settings without losing the current speech state.

        Sensitivity and the pause threshold are live settings, changed mid-talk. Resetting state
        here would drop the speaker mid-sentence every time the user nudged a slider.
        """
        self._config = config
        self._detector.set_sensitivity(config.sensitivity)

    def reset(self) -> None:
        """Return to a clean silent state — on device change or session start."""
        self._detector.reset()
        self._state = SpeechState.SILENT
        self._speech_run = 0
        self._silence_run = 0
        self._pause_fired = False
        self._total_speech_seconds = 0.0
        self._total_silence_seconds = 0.0
