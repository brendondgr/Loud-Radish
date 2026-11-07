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
#: Capture is held. **The recording does not advance while it is** — frames are dropped rather than
#: buffered, so the audio file, the engine's clock and the transcript's timestamps stay in step, and
#: a talk paused for five minutes is five minutes shorter rather than five minutes of silence
#: (D-044). Resuming continues the same session, the same file and the same store.
PAUSED: Final = "paused"
#: Something failed in a way the user has to see. Carries a message; leaves by returning to idle.
ERROR: Final = "error"

RecordState = Literal["idle", "arming", "recording", "paused", "stopping", "processing", "error"]

RECORD_STATES: Final[tuple[RecordState, ...]] = (
    IDLE,
    ARMING,
    RECORDING,
    PAUSED,
    STOPPING,
    PROCESSING,
    ERROR,
)

#: Which run states each mode can actually reach.
#:
#: ``live`` never enters ``processing`` — it has nothing left to do when capture stops.
#: ``recorded`` always processes: that is the whole mode. ``window`` may or may not, depending on
#: whether post-process transcription was switched on, so the state is listed as reachable and
#: simply never entered when it is not.
#:
#: **Every mode arms** since the record sheet (D-075): every recording starts by answering it. Only
#: ``window`` used to, because only it had options; a global shortcut that starts directly still
#: passes straight from ``idle`` to ``recording``.
MODE_STATES: Final[dict[CaptureMode, tuple[RecordState, ...]]] = {
    LIVE: (IDLE, ARMING, RECORDING, PAUSED, STOPPING, ERROR),
    RECORDED: (IDLE, ARMING, RECORDING, PAUSED, STOPPING, PROCESSING, ERROR),
    WINDOW: (IDLE, ARMING, RECORDING, PAUSED, STOPPING, PROCESSING, ERROR),
}

#: States in which a session is doing something and the mode may not be changed.
#:
#: **`paused` belongs here.** A paused session still owns its capture device, its audio file and its
#: transcript store; it is held, not finished. Changing the mode under it would mean the session
#: that resumes is not the session that was paused.
BUSY_STATES: Final[frozenset[str]] = frozenset({ARMING, RECORDING, PAUSED, STOPPING, PROCESSING})

#: What each mode needs beyond a plain ``uv sync``, as the key reported by ``GET /api/health``.
#:
#: **Empty is the common case and is deliberate.** Only ``window`` has a hard requirement, because
#: only it needs a capability the application cannot substitute for. The two audio modes were first
#: written as needing ``audio_device``, and running it showed why that is wrong: the file source
#: replaces a capture device entirely, so on a machine with no ``sounddevice`` — which is what a
#: plain ``uv sync`` produces — every mode was struck through and the application looked broken
#: while working perfectly.
#:
#: A missing capture device is a real problem, but it is an *input source* problem with an existing
#: remedy path: the audio settings tab reports it, and a start that fails for it opens that tab.
#: Disabling the mode as well would be the same failure told twice, once wrongly.
#:
#: The speech model is likewise absent. A session with no model loaded is a recoverable state the
#: interface already explains, not a reason to grey out a button.
MODE_REQUIREMENTS: Final[dict[CaptureMode, tuple[str, ...]]] = {
    LIVE: (),
    RECORDED: (),
    WINDOW: ("window_capture",),
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


# -- capture sources and the mode they imply (D-075) ------------------------------------

#: What a recording listens to, as the record sheet asks it. **Sources are the user's question;
#: modes are the pipeline's answer.** The sheet asks what to record and when to transcribe it, and
#: the mode follows from those two answers rather than being chosen from a tab.
MICROPHONE: Final = "microphone"
#: Everything this machine is playing, reached through a tap (D-030). Never the microphone.
SYSTEM: Final = "system"
#: A window chosen in the desktop's picker. Video, and whichever audio the sheet chose for it.
WINDOW_SOURCE: Final = "window"

CaptureSource = Literal["microphone", "system", "window"]
CAPTURE_SOURCES: Final[tuple[CaptureSource, ...]] = (MICROPHONE, SYSTEM, WINDOW_SOURCE)

# -- audio inputs (D-081) -------------------------------------------------------------------

#: What a run's sound is made of. A run hears one or two of these, mixed, each with its own volume
#: and mute. **Inputs are not sources**: a window is one *source* (what the recording is of) whose
#: sound can be its own, the whole computer's, the microphone's, or its own and the microphone's.
#:
#: Only the matched window's playback, tapped additively (D-027); a window run only.
INPUT_WINDOW: Final = "window"
#: Everything the machine plays, through a tap. Never the microphone (D-030).
INPUT_SYSTEM: Final = "system"
#: The configured microphone, `audio.device_id`.
INPUT_MICROPHONE: Final = "microphone"

AudioInput = Literal["window", "system", "microphone"]
AUDIO_INPUTS: Final[tuple[AudioInput, ...]] = (INPUT_WINDOW, INPUT_SYSTEM, INPUT_MICROPHONE)

#: The most inputs one run mixes. Two is what the record sheet offers; the mixer itself takes any
#: number, and a later plan can raise this without reshaping anything.
MAX_AUDIO_INPUTS: Final = 2

#: The record sheet's older single answer, `audio_source`, as the input it names.
LEGACY_AUDIO_SOURCE: Final[dict[str, AudioInput]] = {
    "application": INPUT_WINDOW,
    "system": INPUT_SYSTEM,
    "microphone": INPUT_MICROPHONE,
}


#: What a request naming only a mode means. These are the three combinations the header's tabs used
#: to offer, kept so that a global shortcut sending `toggle --mode recorded` records exactly what it
#: always did. An empty ``audio_source`` defers to ``capture.audio_source`` in configuration, which
#: is what a window request without options has always done.
MODE_PRESETS: Final[dict[CaptureMode, dict[str, object]]] = {
    LIVE: {
        "source": MICROPHONE,
        "live_transcription": True,
        "post_transcription": False,
        "video": False,
        "audio_source": "",
    },
    RECORDED: {
        "source": MICROPHONE,
        "live_transcription": False,
        "post_transcription": True,
        "video": False,
        "audio_source": "",
    },
    WINDOW: {
        "source": WINDOW_SOURCE,
        "live_transcription": True,
        "post_transcription": True,
        "video": True,
        "audio_source": "",
    },
}


def derive_mode(source: str, *, post_transcription: bool) -> CaptureMode:
    """The mode a set of record-sheet answers runs as.

    A window is always ``window``: only it can have video, a portal, and a monitor pane. For the
    two audio sources the mode follows the second pass, because that is what ``recorded`` has
    always meant to the rest of the application — a session whose finished transcript arrives
    after it stops. Live text alongside it is now allowed, so "microphone, both" is ``recorded``
    with an engine running, and the session keeps both passes as two revisions (D-022).
    """
    if source == WINDOW_SOURCE:
        return WINDOW
    if source not in CAPTURE_SOURCES:
        raise ValueError(f"Unknown capture source: {source!r}")
    return RECORDED if post_transcription else LIVE
