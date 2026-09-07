"""Key sequences, in the three spellings this application has to hold at once.

A shortcut is written three ways and they must agree exactly:

1. **`"Ctrl+Alt+Shift+F9"`** — what the config file stores and a person reads.
2. **`0x0F000038`** — a single integer, `Qt::KeyboardModifiers | Qt::Key`, which is what
   `org.kde.KGlobalAccel` speaks over D-Bus.
3. the physical keys, which are neither of the above and are somebody else's problem.

**The integer form has a trap, and it cost most of a session.** Every non-printing key carries a
`0x01000000` prefix — `Qt::Key_F9` is `0x01000038`, not `0x38`, because `0x38` is the digit *8*.
Drop the prefix and `Ctrl+Alt+Shift+F9` silently becomes `Ctrl+Alt+Shift+8`: KDE accepts it, stores
it, reports it bound, resolves it by key, and reports the component active — and then the shortcut
never fires, because nothing is pressing the key that was actually registered. Nothing in the
round trip contradicts you. What finally showed it was reading `~/.config/kglobalshortcutsrc` and
seeing `_launch=Ctrl+Alt+Shift+8` written where `F9` was meant.

That is why this module exists as its own file with its own tests, rather than as two helpers next
to the registration code: the encoding is the part that is wrong in a way nothing else notices.
"""

from __future__ import annotations

from typing import Final

#: `Qt::KeyboardModifier`. The order here is the order KDE writes them in.
MODIFIERS: Final[dict[str, int]] = {
    "Meta": 0x10000000,
    "Ctrl": 0x04000000,
    "Alt": 0x08000000,
    "Shift": 0x02000000,
}

#: Spellings accepted on input but never produced. `Super` and `Win` are what people type.
MODIFIER_ALIASES: Final[dict[str, str]] = {
    "super": "Meta",
    "win": "Meta",
    "logo": "Meta",
    "control": "Ctrl",
    "cmd": "Meta",
    "option": "Alt",
}

#: The `0x01000000` family — every key that does not stand for a character.
NAMED_KEYS: Final[dict[str, int]] = {
    "Escape": 0x01000000,
    "Tab": 0x01000001,
    "Backtab": 0x01000002,
    "Backspace": 0x01000003,
    "Return": 0x01000004,
    "Enter": 0x01000005,
    "Insert": 0x01000006,
    "Delete": 0x01000007,
    "Pause": 0x01000008,
    "Print": 0x01000009,
    "SysReq": 0x0100000A,
    "Clear": 0x0100000B,
    "Home": 0x01000010,
    "End": 0x01000011,
    "Left": 0x01000012,
    "Up": 0x01000013,
    "Right": 0x01000014,
    "Down": 0x01000015,
    "PgUp": 0x01000016,
    "PgDown": 0x01000017,
    "CapsLock": 0x01000024,
    "NumLock": 0x01000025,
    "ScrollLock": 0x01000026,
    "Menu": 0x01000055,
    "Space": 0x20,
}

#: F1 is `0x01000030`; the rest follow it. Qt stops at F35.
_F1: Final = 0x01000030
_MAX_F: Final = 35


class KeyError_(ValueError):
    """A sequence that cannot be read. Named so it is not the builtin."""


def _key_to_int(name: str) -> int:
    """One key name to its `Qt::Key` value."""
    for known, value in NAMED_KEYS.items():
        if known.lower() == name.lower():
            return value
    if len(name) > 1 and name[0] in "fF" and name[1:].isdigit():
        number = int(name[1:])
        if 1 <= number <= _MAX_F:
            return _F1 + number - 1
        raise KeyError_(f"Qt has no key {name!r}; function keys stop at F{_MAX_F}.")
    if len(name) == 1:
        # Letters are their **uppercase** code point: Qt::Key_A is 0x41. Digits and punctuation
        # are simply themselves.
        return ord(name.upper())
    raise KeyError_(f"{name!r} is not a key this can encode.")


def _int_to_key(value: int) -> str:
    """One `Qt::Key` value back to its name. The inverse of :func:`_key_to_int`."""
    for name, known in NAMED_KEYS.items():
        if known == value:
            return name
    if _F1 <= value <= _F1 + _MAX_F - 1:
        return f"F{value - _F1 + 1}"
    if 0x20 <= value <= 0x7E:
        return chr(value).upper()
    raise KeyError_(f"0x{value:08X} is not a key this can name.")


def parse_sequence(text: str) -> int:
    """``"Ctrl+Alt+Shift+F9"`` to the single integer KGlobalAccel wants.

    Raises:
        KeyError_: if any part is unreadable. **Deliberately not a best guess** — a shortcut that
            silently becomes a different shortcut is exactly the failure this module was written
            after, and one that refuses is a message where the other is a mystery.
    """
    cleaned = text.strip()
    if not cleaned:
        raise KeyError_("An empty string is not a key sequence.")

    parts = [part.strip() for part in cleaned.split("+") if part.strip()]
    if not parts:
        raise KeyError_(f"{text!r} is not a key sequence.")

    # "Ctrl++" is Ctrl plus the *plus key*; splitting on "+" ate it, so put it back. A single
    # trailing "+" ("Ctrl+") is an unfinished sequence and falls through to the refusal below.
    if cleaned.endswith("++"):
        parts.append("+")
    elif cleaned.endswith("+"):
        raise KeyError_(f"{text!r} names modifiers but no key.")

    value = 0
    for part in parts[:-1]:
        canonical = MODIFIER_ALIASES.get(part.lower(), part).lower()
        match = next((name for name in MODIFIERS if name.lower() == canonical), None)
        if match is None:
            raise KeyError_(f"{part!r} is not a modifier. Use {', '.join(MODIFIERS)}.")
        value |= MODIFIERS[match]
    return value | _key_to_int(parts[-1])


def format_sequence(value: int) -> str:
    """The integer back to ``"Ctrl+Alt+Shift+F9"``, in the order KDE writes it."""
    parts = [name for name, bit in MODIFIERS.items() if value & bit]
    parts.append(_int_to_key(value & ~sum(MODIFIERS.values())))
    return "+".join(parts)


def is_readable(text: str) -> bool:
    """Whether :func:`parse_sequence` would accept it. For validating before saving."""
    try:
        parse_sequence(text)
    except KeyError_:
        return False
    return True
