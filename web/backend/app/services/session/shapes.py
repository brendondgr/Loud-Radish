"""The small shapes and tuning numbers a session shares across its modules.

A leaf: this imports nothing from its siblings, which is what lets ``manager.py`` and each of the
mixin modules split out of it — ``frames``, ``background``, ``window_capture``, ``passes`` and
``sources`` — all import from here without a cycle.

Everything here was module-level in ``manager.py`` before that file passed twice its 800-line cap.
The values are unchanged and the comments explaining each are the originals: what a number is for
is the expensive part to reconstruct, and none of it was re-derived during the move.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

import numpy as np

#: How many times one session's video capture may be reopened after dying (D-036). Above this the
#: video ends with a message: a portal that has failed five times is not coming back, and a loop
#: that keeps asking it is one that fills the recording folder with empty segments.
MAX_CAPTURE_RESUMES: Final = 5

#: The shortest interval between two attempts. A capture that dies the instant it starts would
#: otherwise burn the whole allowance in a second, and report a permanent failure for what was a
#: transient one.
RESUME_BACKOFF_S: Final = 5.0

#: Emits one transport event. Called from worker threads.
EmitFn = Callable[[str, dict[str, Any]], None]

#: Roughly four level updates a second is plenty; more just drives the frontend's render loop.
LEVEL_INTERVAL_S = 0.25

#: How often health telemetry is published.
STATUS_INTERVAL_S = 1.0

#: Absolute level a monitored frame must reach to count as worth transcribing, as linear RMS.
#: −33 dBFS, chosen from measurement rather than taste: seminar speech has a 35 dB dynamic range
#: and peaks well above this, while a video's background music measured a flat 5 dB band from
#: −42.6 to −37.4 dBFS and stays below it. Speech has dynamics; ambience does not.
LOOPBACK_SPEECH_RMS = 0.022

#: How long each side of the dead-tap comparison listens. One second is ample: the question is
#: whether anything at all arrives, not what it sounds like, and both probes run before the
#: session starts — so this is time the user waits at the moment they press record.
TAP_PROBE_S = 1.0

#: Capture queue depth, in frames. At 32 ms a frame this is about six seconds of slack — enough to
#: absorb a slow inference pass, short enough that a sustained problem surfaces quickly.
QUEUE_CAPACITY = 200


@dataclass(frozen=True)
class CaptureOptions:
    """The three per-run switches a `window` session was armed with (D-020).

    A plain dataclass rather than the request schema: `services/` must not import `schemas/`, which
    is a validation boundary for HTTP and not a vocabulary for the pipeline.
    """

    live_transcription: bool = True
    post_transcription: bool = True
    video: bool = True
    #: Which audio this run records: `system` (everything the machine plays), `application` (only
    #: the matched application, tapped additively), or `microphone`. Per-run, and it **overrides**
    #: `capture.audio_source` — the sheet in front of the user at the moment they press record is
    #: more authoritative than a setting they configured once and forgot.
    audio_source: str = "system"

    @property
    def records_nothing(self) -> bool:
        return not (self.live_transcription or self.post_transcription or self.video)


@dataclass
class CapturedFrame:
    """One frame, with what the VAD made of it."""

    audio: np.ndarray
    speaking: bool
    pause: bool


class SessionError(RuntimeError):
    """Raised when a session operation cannot proceed."""
