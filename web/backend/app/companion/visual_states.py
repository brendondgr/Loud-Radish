"""Which picture the instrument shows, from the capture mode *and* the run state (D-020, D-022).

**This is the specification's central structural requirement**, and it is the reason this file
exists rather than a dictionary keyed on run state alone. `docs/motion-spec.md` draws *Live
transcription* and *Recording* as genuinely different pictures — a travelling teal sine against a
red grille pulsing as one body — and in the run-state vocabulary those are the **same state in
different modes**. A map keyed on run state alone would collapse them and lose the distinction the
whole design is built around.

Kept as data rather than branching for the same reason `MODE_STATES` is: the pairing is then
testable in one place instead of scattered through render conditions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .. import branding
from ..services.session import modes

# -- the six pictures --------------------------------------------------------------------

IDLE: Final = "idle"
LIVE: Final = "live"
RECORDING: Final = "recording"
TRANSCRIBING: Final = "transcribing"
REWRITING: Final = "rewriting"
FAULT: Final = "fault"

#: Server unreachable. Not in the specification — it has no state for "the thing behind the icon is
#: not there" — so it borrows the fault treatment, which is the honest one: something is wrong and
#: nothing is being recorded.
SERVER_DOWN: Final = "server-down"


@dataclass(frozen=True)
class VisualState:
    """One picture: its hue, its grille motion, and how fast that motion runs."""

    name: str
    #: As given in the specification, in oklch. Grey at rest — the instrument is colourless unless
    #: it is doing work on the user's behalf, which is the design's governing rule.
    hue: str
    #: Which of the six grille behaviours: breathe, wave, hold, settle, ripple, or stubs.
    motion: str
    #: Seconds for one cycle.
    period: float
    #: Which of the three base LEDs are lit, left to right.
    leds: tuple[bool, bool, bool]
    description: str


VISUALS: Final[dict[str, VisualState]] = {
    IDLE: VisualState(
        IDLE,
        "#8A8F95",
        "breathe",
        5.2,
        (False, False, False),
        "standby · mic closed",
    ),
    LIVE: VisualState(
        LIVE,
        "oklch(0.68 0.09 195)",
        "wave",
        1.5,
        (True, False, False),
        "listening · writing",
    ),
    RECORDING: VisualState(
        RECORDING,
        "oklch(0.58 0.14 25)",
        "hold",
        1.05,
        (False, True, False),
        "capturing · not reading",
    ),
    TRANSCRIBING: VisualState(
        TRANSCRIBING,
        "oklch(0.7 0.11 75)",
        "settle",
        2.0,
        (False, False, True),
        "decoding · no longer listening",
    ),
    REWRITING: VisualState(
        REWRITING,
        "oklch(0.6 0.1 300)",
        "ripple",
        2.8,
        (False, True, True),
        "thinking · not hearing",
    ),
    FAULT: VisualState(
        FAULT,
        "oklch(0.55 0.1 40)",
        "stubs",
        3.6,
        (False, False, False),
        "no audio reaching the capsule",
    ),
    SERVER_DOWN: VisualState(
        SERVER_DOWN,
        "oklch(0.55 0.1 40)",
        "stubs",
        3.6,
        (False, False, False),
        f"{branding.APP_NAME} is not running",
    ),
}


def visual_for(
    mode: str, state: str, *, running_pass: bool = False, dictation: str = ""
) -> VisualState:
    """The picture for one (capture mode, run state) pair.

    ``running_pass`` marks a *polish* pass running in the background — the specification's
    *Rewriting* state, which has no run state of its own because it happens **during** a session
    rather than as a phase of one. It is deliberately given lower precedence than recording: an
    instrument that stopped looking like it was recording because a background rewrite started
    would be lying about the thing that matters most.

    ``dictation`` takes precedence over everything but a fault, and that is the point of it. A
    dictation is started by a keystroke with no window open and nothing else on screen to say it
    is listening; if the icon does not show it, **there is no way to tell whether the microphone is
    live**. Reported exactly that way. The two cannot overlap — the service refuses to start a
    dictation while a session is running — so this steals nothing from the session states.
    """
    if state == modes.ERROR:
        return VISUALS[FAULT]

    if dictation:
        # Listening reads as *live*: the microphone is open and the words are being taken down.
        # Everything after the microphone closes is the transcribing picture, because that is what
        # is happening — including the tidy pass and the paste.
        return VISUALS[LIVE] if dictation == "recording" else VISUALS[TRANSCRIBING]

    if state == modes.PROCESSING:
        return VISUALS[TRANSCRIBING]

    if state in (modes.RECORDING, modes.STOPPING):
        # The distinction the specification is built around. `live` mode and a window capture that
        # is transcribing as it goes are *reading*; a recorded capture is only *listening*.
        if mode == modes.LIVE:
            return VISUALS[LIVE]
        if mode == modes.WINDOW:
            return VISUALS[LIVE]
        return VISUALS[RECORDING]

    if state == modes.ARMING:
        # No entry in the specification. The front half of its Idle → Listening transition, held:
        # the hue of what is about to happen, over the grille of what is happening now.
        return VISUALS[IDLE]

    if running_pass:
        return VISUALS[REWRITING]

    return VISUALS[IDLE]
