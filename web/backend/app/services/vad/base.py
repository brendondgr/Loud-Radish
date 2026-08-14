"""The voice activity detector interface (BE §5).

A VAD is a very small, very fast model answering one question per short frame: *is someone speaking
right now?* It costs almost nothing next to a full speech model, and it earns its place three times
over:

1. **Skip silence.** Do not run the expensive model on nothing.
2. **Prevent hallucination.** Autoregressive models invent text when fed silence — phantom phrases
   learned from training-data subtitles. Gating on the VAD is the single most effective mitigation.
3. **Find safe cut points.** Speakers pause between clauses, and those pauses are places where
   cutting the audio will not slice a word in half.

Detectors answer only the per-frame question. Hysteresis and pause detection live above this
interface, in ``hysteresis.py``, so every detector shares one implementation of the part that is
easy to get subtly wrong.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class VoiceActivityDetector(ABC):
    """Answers, per frame, whether the frame contains speech."""

    @abstractmethod
    def is_speech(self, frame: np.ndarray) -> bool:
        """Whether ``frame`` — canonical-format audio — contains speech."""

    @abstractmethod
    def set_sensitivity(self, sensitivity: float) -> None:
        """Adjust sensitivity, 0.0 (least sensitive) to 1.0 (most sensitive).

        Exposed as a labelled slider rather than a raw threshold: a noisy hall needs a different
        setting from a quiet one, and the user does not know what 0.6 means (FE §7.1).
        """

    @abstractmethod
    def reset(self) -> None:
        """Discard any adaptive state. Called when the device or session changes."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, surfaced in status output."""

    def score(self, frame: np.ndarray) -> float:
        """Optional 0.0–1.0 speech likelihood, for diagnostics and threshold tuning.

        The default derives a coarse score from the boolean answer. Detectors that compute a real
        probability should override it.
        """
        return 1.0 if self.is_speech(frame) else 0.0
