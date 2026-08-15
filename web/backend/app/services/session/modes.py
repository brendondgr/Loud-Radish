"""Capture modes and run states — the vocabulary both sides of the wire agree on (D-020).

The application used to have one thing it could be doing, expressed as a boolean: recording, or not.
Three modes and a post-processing stage do not fit in a boolean, and the names have to mean the same
thing in Python and in the browser or the interface will confidently show the wrong state.

So the vocabulary is defined here and mirrored in ``web/frontend/static/js/core/modes.js``, and
``tests/utils/test_mode_vocabulary.py`` parses that file and asserts the two are identical. The
mirror is possible only because the frontend has no build step — the JavaScript the browser runs is
a file on disk a test can read.

**Modes** are what kind of recording this is. **Run states** are where a recording has got to. They
are separate because the interface gives them separate controls: choosing a mode and advancing a
recording are different intentions, and a single control that did both would change meaning under
the user's finger.
"""

from __future__ import annotations

from typing import Final, Literal

# -- capture modes ----------------------------------------------------------------------

#: Continuous capture, transcribed as it arrives. The original behaviour.
LIVE: Final = "live"
#: Capture to a file with no inference; transcribe the whole file once it stops.
RECORDED: Final = "recorded"
#: Capture a chosen window, with live transcription, post-process transcription, and video each
#: switchable before capture begins.
WINDOW: Final = "window"

CaptureMode = Literal["live", "recorded", "window"]

CAPTURE_MODES: Final[tuple[CaptureMode, ...]] = (LIVE, RECORDED, WINDOW)

# -- run states -------------------------------------------------------------------------

#: Nothing is running. The only state from which the mode may be changed.
IDLE: Final = "idle"
#: Options are being collected, or the desktop is asking the user to choose a window. Nothing has
#: been captured yet and cancelling here costs nothing.
ARMING: Final = "arming"
#: Capture is running.
RECORDING: Final = "recording"
#: Capture has been asked to stop and is closing its files. Brief, and not interruptible.
STOPPING: Final = "stopping"
#: Capture has finished and a transcription pass is running over what it produced. Can last longer
#: than the recording did.
PROCESSING: Final = "processing"
#: Something failed in a way the user has to see. Carries a message; leaves by returning to idle.
ERROR: Final = "error"

RecordState = Literal["idle", "arming", "recording", "stopping", "processing", "error"]

RECORD_STATES: Final[tuple[RecordState, ...]] = (
    IDLE,
    ARMING,
    RECORDING,
    STOPPING,
    PROCESSING,
    ERROR,
)

#: Which run states each mode can actually reach.
#:
#: ``live`` never enters ``processing`` — it has nothing left to do when capture stops — and never
#: enters ``arming``, because it has no options to collect. ``recorded`` always processes: that is
#: the whole mode. ``window`` may or may not, depending on whether post-process transcription was
#: switched on, so the state is listed as reachable and simply never entered when it is not.
MODE_STATES: Final[dict[CaptureMode, tuple[RecordState, ...]]] = {
    LIVE: (IDLE, RECORDING, STOPPING, ERROR),
    RECORDED: (IDLE, RECORDING, STOPPING, PROCESSING, ERROR),
    WINDOW: (IDLE, ARMING, RECORDING, STOPPING, PROCESSING, ERROR),
}

#: States in which a session is doing something and the mode may not be changed.
BUSY_STATES: Final[frozenset[str]] = frozenset({ARMING, RECORDING, STOPPING, PROCESSING})

#: What each mode needs beyond a plain ``uv sync``, as the key reported by ``GET /api/health``.
#:
#: ``live`` and ``recorded`` share the same requirement because both capture a device; the speech
#: model is *not* listed, because a session with no model loaded is a recoverable state the
#: interface already explains rather than a reason to hide the mode.
MODE_REQUIREMENTS: Final[dict[CaptureMode, tuple[str, ...]]] = {
    LIVE: ("audio_device",),
    RECORDED: ("audio_device",),
    WINDOW: ("audio_device", "window_capture"),
}


def states_for(mode: str) -> tuple[RecordState, ...]:
    """The run states ``mode`` can reach. Raises on a mode that does not exist."""
    try:
        return MODE_STATES[mode]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"Unknown capture mode: {mode!r}") from exc


def is_valid_mode(mode: str) -> bool:
    """Whether ``mode`` names a capture mode."""
    return mode in MODE_STATES


def is_busy(state: str) -> bool:
    """Whether a session in ``state`` is doing something that must not be interrupted."""
    return state in BUSY_STATES
