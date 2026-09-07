"""Sending a keystroke to whatever window has focus.

Named for the keystroke rather than for pasting, because `app.desktop.paste` as a module and
`paste()` as a function shadow each other the moment the package exports the function — and a test
that imports the module and gets the function fails in a way that reads like a missing attribute.

Two routes, and **the order was decided by a measurement that reversed the obvious answer**:

1. **`ydotool`** — a virtual input device through `/dev/uinput`. Needs `ydotoold` running. Verified
   by pasting into a real focused window and reading the text back out of it.
2. **`wtype`** — Wayland's virtual-keyboard protocol. KWin exposes it and it was the natural first
   choice, being the Wayland-native tool that needs no daemon and no `/dev/uinput` permissions.

`wtype` is second because when both were tried against the same focused window, one after the
other, **`ydotool` pasted and `wtype` did nothing — and both returned exit status 0.** A backend
that reports success while having no effect is worse than one that fails, because the caller then
believes the words were delivered and stops. So the one that was watched working goes first, and
`wtype` remains only for a machine that has no `ydotool`, where something untrustworthy still beats
nothing.

**Why a keystroke and not typing the text.** `wtype` can type a string directly, which would avoid
the clipboard entirely — and is the wrong choice here. It sends one key event per character, so a
paragraph of dictation arrives as a visible stream over several seconds, any keystroke the user
makes in the middle interleaves with it, and characters outside the current keyboard layout come
out wrong. A clipboard write plus one chord is atomic from the application's point of view.

**The terminal problem is real and is not solved by guessing.** In a terminal emulator `Ctrl+V` is
"quote the next character", and paste is `Ctrl+Shift+V`. There is no reliable way to ask the
compositor what kind of window has focus — so the chord is a *setting* rather than a detection, and
the default is the one that is right nearly everywhere.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .outcome import Outcome

logger = logging.getLogger(__name__)

TIMEOUT_S = 5.0

#: Linux input event codes, for `ydotool`, which speaks the kernel's numbers rather than key names.
EVDEV_CODES = {
    "ctrl": 29,
    "shift": 42,
    "alt": 56,
    "meta": 125,
    "v": 47,
    "insert": 110,
}


def _split_chord(chord: str) -> tuple[list[str], str]:
    """``"ctrl+shift+v"`` to its modifiers and its key, lowercased."""
    parts = [part.strip().lower() for part in chord.split("+") if part.strip()]
    if not parts:
        return [], ""
    return parts[:-1], parts[-1]


def _via_wtype(chord: str) -> Outcome | None:
    tool = shutil.which("wtype")
    if not tool or not os.environ.get("WAYLAND_DISPLAY"):
        return None
    modifiers, key = _split_chord(chord)
    if not key:
        return Outcome(False, "wtype", f"{chord!r} names no key")

    arguments = [tool]
    for modifier in modifiers:
        arguments += ["-M", modifier]
    arguments += ["-k", key]
    for modifier in reversed(modifiers):
        arguments += ["-m", modifier]

    try:
        subprocess.run(arguments, check=True, timeout=TIMEOUT_S)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:
        return Outcome(False, "wtype", str(exc))
    return Outcome(True, "wtype")


def _via_ydotool(chord: str) -> Outcome | None:
    tool = shutil.which("ydotool")
    if not tool:
        return None
    modifiers, key = _split_chord(chord)
    codes = [EVDEV_CODES.get(name) for name in [*modifiers, key]]
    if not key or any(code is None for code in codes):
        return Outcome(False, "ydotool", f"{chord!r} has a key this cannot translate")

    presses = [f"{code}:1" for code in codes]
    releases = [f"{code}:0" for code in reversed(codes)]
    environment = dict(os.environ)
    # ydotool's daemon socket is per-user and not always in the tool's default place.
    environment.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")

    try:
        subprocess.run(  # noqa: S603
            [tool, "key", "-d", "20", *presses, *releases],
            check=True,
            timeout=TIMEOUT_S,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Outcome(False, "ydotool", str(exc))
    return Outcome(True, "ydotool")


#: Order matters and is not alphabetical — see the module docstring. `ydotool` was watched
#: working; `wtype` returns 0 whether or not anything happened. Names rather than functions, for
#: the reason given in `clipboard.py`.
BACKENDS = ("ydotool", "wtype")


def _backend(name: str):  # noqa: ANN202
    return {"ydotool": _via_ydotool, "wtype": _via_wtype}[name]


def paste(chord: str = "ctrl+v") -> Outcome:
    """Send ``chord`` to the focused window. Never raises.

    The caller is expected to have put the text on the clipboard already, and to have confirmed it
    landed — pressing paste over a clipboard that still holds the *previous* thing is worse than
    doing nothing, because it looks like it worked.
    """
    attempted: list[str] = []
    for name in BACKENDS:
        result = _backend(name)(chord)
        if result is None:
            continue
        attempted.append(name)
        if result.ok:
            return Outcome(True, result.backend, chord, attempts=attempted)
        logger.debug("Paste backend %s failed: %s", name, result.detail)

    return Outcome(False, detail="nothing here can type into another window", attempts=attempted)


def available() -> bool:
    """Whether any paste backend exists. For telling the user before they rely on it."""
    return bool(shutil.which("wtype") or shutil.which("ydotool"))
