"""The parts of the settings window that are not a window.

Everything here is a pure function over plain data: a key event becomes a sequence string, a
sequence is checked against what the desktop already holds, and a set of edits becomes the payload
`PATCH /api/config` expects. **That is the whole point of the split.** Tk's main loop cannot be
driven from a test, so anything worth testing has to live where no main loop is needed — and what is
worth testing here is exactly the arithmetic that was wrong last time: turning what someone pressed
into the string that gets stored.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import keys

#: X11 modifier bits, as Tk reports them in ``event.state``.
#:
#: **Mod4 is the Meta/Super key on every desktop this targets**, but X11 does not promise that: the
#: mapping is whatever `xmodmap` says. Reading it properly would mean a second X connection for a
#: value that has been Mod4 on every Linux desktop for twenty years, so it is a constant with this
#: note attached rather than a lookup.
SHIFT, CONTROL, ALT, META = 0x0001, 0x0004, 0x0008, 0x0040

#: Bits that mean a lock is on rather than a key is held. Including them would make a shortcut
#: captured with Caps Lock on differ from the same keys captured without it.
IGNORED_BITS = 0x0002 | 0x0010 | 0x0080

#: Tk keysym → the name `keys.parse_sequence` understands. Only where they differ.
KEYSYM_NAMES: dict[str, str] = {
    "prior": "PgUp",
    "next": "PgDown",
    "backspace": "Backspace",
    "escape": "Escape",
    "return": "Return",
    "kp_enter": "Enter",
    "tab": "Tab",
    "space": "Space",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "left": "Left",
    "right": "Right",
    "up": "Up",
    "down": "Down",
    "print": "Print",
    "menu": "Menu",
}

#: Keysyms that are only ever modifiers. Pressing one alone is not a shortcut.
MODIFIER_KEYSYMS = frozenset(
    {
        "shift_l",
        "shift_r",
        "control_l",
        "control_r",
        "alt_l",
        "alt_r",
        "super_l",
        "super_r",
        "meta_l",
        "meta_r",
        "caps_lock",
        "num_lock",
        "iso_level3_shift",
        "mode_switch",
    }
)


class NotAShortcut(ValueError):
    """What was pressed cannot be a shortcut, and why."""


def chord_from_event(keysym: str, state: int) -> str:
    """A Tk key event to a sequence string such as ``"Meta+Alt+D"``.

    Raises:
        NotAShortcut: for a modifier pressed on its own, an unmappable key, or a bare letter. The
            last is deliberate: a global shortcut with no modifier takes that key away from every
            other application on the desktop, and someone capturing "D" almost certainly meant to
            hold something down as well.
    """
    name = (keysym or "").strip()
    if not name:
        raise NotAShortcut("No key was pressed.")
    if name.lower() in MODIFIER_KEYSYMS:
        raise NotAShortcut("Hold the modifiers and press a key as well.")

    parts = [
        label
        for bit, label in ((META, "Meta"), (CONTROL, "Ctrl"), (ALT, "Alt"), (SHIFT, "Shift"))
        if state & ~IGNORED_BITS & bit
    ]

    key = KEYSYM_NAMES.get(name.lower())
    if key is None:
        if len(name) == 1:
            key = name.upper()
        elif name[0] in "fF" and name[1:].isdigit():
            key = f"F{int(name[1:])}"
        else:
            raise NotAShortcut(f"{name} is not a key this can use in a shortcut.")

    if not parts and not (key.startswith("F") and key[1:].isdigit()):
        raise NotAShortcut(
            "Hold Meta, Ctrl or Alt as well — a shortcut with no modifier takes that key "
            "away from every other application."
        )

    sequence = "+".join([*parts, key])
    if not keys.is_readable(sequence):
        raise NotAShortcut(f"{sequence} cannot be stored as a shortcut.")
    return sequence


def conflict_for(
    sequence: str, action: str, current: dict[str, str], holder: Callable[[str], str] | None = None
) -> str:
    """Who already has ``sequence``, in words, or empty if nobody does.

    Checks this application's own other shortcuts first and without touching the bus, because two
    of ours colliding is the mistake most easily made in a settings window and the desktop would
    report it as *us* holding the key, which reads like a bug rather than a duplicate.
    """
    for other, existing in current.items():
        if other != action and existing and existing.lower() == sequence.lower():
            return f"your own {other.replace('_', ' ')} shortcut"
    if holder is None:
        return ""
    return holder(sequence)


def patch_payload(edits: dict[str, Any]) -> dict[str, Any]:
    """The body `PATCH /api/config` expects, from a flat map of dotted paths.

    The settings window never writes a config file itself. The backend owns configuration, the
    frontend reads it and writes changes back — and a native window is not the exception to that
    rule just because it is not a browser.
    """
    return {"changes": dict(edits)}


def shortcut_edits(values: dict[str, str]) -> dict[str, Any]:
    """Shortcut fields to dotted paths, dropping anything unchanged or unreadable."""
    return {
        f"shortcuts.{action}": sequence
        for action, sequence in values.items()
        if sequence and keys.is_readable(sequence)
    }
