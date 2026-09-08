"""Key sequences round-tripping between what a person writes and what KDE stores (D-047).

**This module exists because of one bug, and the bug is the reason every case below is here.**
`Qt::Key_F9` is `0x01000038`; drop the `0x01000000` prefix that marks a non-printing key and you
get `0x38`, which is the digit *8*. Registering `Ctrl+Alt+Shift+F9` that way binds
`Ctrl+Alt+Shift+8` instead — and KDE accepts it, stores it, reports it bound, resolves it by key,
and reports the component active. Every check passes. The shortcut simply never fires, because
nothing presses the key that was really registered.

Nothing in the D-Bus round trip contradicts you, so the tests that matter most here are the ones
that pin an encoding against a value observed coming *out* of KDE rather than one this repository
computed for itself.
"""

from __future__ import annotations

import pytest
from app.companion.keys import (
    KeyError_,
    display_sequence,
    format_sequence,
    is_readable,
    parse_sequence,
)

# -- pinned against KDE itself -------------------------------------------------------------


def test_alt_f4_matches_what_kde_stores_for_window_close() -> None:
    """**The anchor.** `shortcutKeys` for kwin's "Window Close" returns `150994995` on a real
    session bus. That number was read off this machine, not derived here, so it is the one value
    in this file that can contradict the implementation rather than agree with it by construction.
    """
    assert parse_sequence("Alt+F4") == 150994995
    assert parse_sequence("Alt+F4") == 0x09000033


def test_a_function_key_keeps_its_non_printing_prefix() -> None:
    """The bug, as a test. Without `0x01000000` this is `Ctrl+Alt+Shift+8`."""
    value = parse_sequence("Ctrl+Alt+Shift+F9")

    assert value == 0x0F000038
    assert value & 0x01000000, "F9 lost the prefix that distinguishes it from the digit 8"


def test_the_digit_eight_is_not_the_same_as_f9() -> None:
    """The two the bug confused, side by side."""
    assert parse_sequence("Ctrl+Alt+Shift+8") == 0x0E000038
    assert parse_sequence("Ctrl+Alt+Shift+F9") == 0x0F000038
    assert parse_sequence("Ctrl+Alt+Shift+8") != parse_sequence("Ctrl+Alt+Shift+F9")


# -- round trips ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sequence",
    [
        "Meta+Alt+L",
        "Meta+Alt+R",
        "Meta+Alt+W",
        "Meta+Alt+S",
        "Meta+Alt+T",
        "Meta+Alt+D",
        "Ctrl+Alt+Shift+F9",
        "Shift+Print",
        "Alt+F4",
        "Meta+Alt+Space",
        "Ctrl+Delete",
        "Meta+PgUp",
        "Ctrl+;",
        "F1",
        "F35",
    ],
)
def test_a_sequence_survives_the_trip_to_an_integer_and_back(sequence: str) -> None:
    assert format_sequence(parse_sequence(sequence)) == sequence


def test_every_shipped_default_round_trips() -> None:
    """The defaults are the sequences most likely to be encoded, so they are pinned as a set."""
    from app.config.schema import ShortcutsConfig

    config = ShortcutsConfig()
    for field in type(config).model_fields:
        sequence = getattr(config, field)
        if isinstance(sequence, str) and sequence:
            assert format_sequence(parse_sequence(sequence)) == sequence, field


# -- modifiers -----------------------------------------------------------------------------


def test_modifier_order_is_normalised_to_the_order_kde_writes() -> None:
    """A person may type them in any order; the file should not then differ from KDE's own."""
    assert format_sequence(parse_sequence("Alt+Meta+L")) == "Meta+Alt+L"
    assert format_sequence(parse_sequence("Shift+Ctrl+Alt+F9")) == "Ctrl+Alt+Shift+F9"


@pytest.mark.parametrize("spelling", ["Super+L", "Win+L", "Logo+L", "meta+l", "META+L"])
def test_the_names_people_type_for_the_meta_key_are_accepted(spelling: str) -> None:
    assert parse_sequence(spelling) == parse_sequence("Meta+L")


def test_a_bare_key_needs_no_modifier() -> None:
    assert parse_sequence("F5") == 0x01000034


# -- refusing rather than guessing -----------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "Hyper+L", "Ctrl+NotAKey", "F36", "F0", "Ctrl+"],
)
def test_an_unreadable_sequence_is_refused(bad: str) -> None:
    """**Never a best guess.** A shortcut that quietly becomes a different shortcut is the whole
    reason this module exists; one that refuses produces a message instead of a mystery."""
    with pytest.raises(KeyError_):
        parse_sequence(bad)
    assert not is_readable(bad)


def test_a_function_key_past_the_end_says_where_the_end_is() -> None:
    with pytest.raises(KeyError_, match="F35"):
        parse_sequence("F36")


def test_an_unknown_modifier_lists_the_ones_that_exist() -> None:
    with pytest.raises(KeyError_, match="Meta"):
        parse_sequence("Hyper+L")


# -- what a person is shown --------------------------------------------------------------------


def test_the_windows_key_is_called_the_windows_key() -> None:
    """**Reported as "I have no idea what Meta is", and fairly.** Qt and KDE call the Windows key
    `Meta`; nothing outside a Qt codebase does. The stored form keeps KDE's name so it matches what
    System Settings shows, and only the label changes."""
    assert display_sequence("Meta+Shift+Space") == "Win+Shift+Space"
    assert display_sequence("Meta+Alt+D") == "Win+Alt+D"


def test_the_label_is_not_what_gets_registered() -> None:
    """`format_sequence` is the canonical form and is what reaches KGlobalAccel and the config
    file; `display_sequence` is a label and never round-trips back into either."""
    assert format_sequence(parse_sequence("Meta+Shift+Space")) == "Meta+Shift+Space"
    assert display_sequence("Meta+Shift+Space") != format_sequence(0x12000020)


def test_typing_the_label_back_in_still_works() -> None:
    """A pleasant accident worth keeping: `Win` was already an accepted input alias, so somebody
    who copies the label out of the settings window into the config file gets what they meant
    rather than an error."""
    assert is_readable("Win+Shift+Space")
    assert parse_sequence("Win+Shift+Space") == parse_sequence("Meta+Shift+Space")


def test_the_other_modifiers_are_already_called_what_people_call_them() -> None:
    assert display_sequence("Ctrl+Alt+Shift+F9") == "Ctrl+Alt+Shift+F9"


def test_an_integer_can_be_shown_directly() -> None:
    assert display_sequence(0x12000020) == "Win+Shift+Space"


def test_the_chord_the_user_asked_for_round_trips() -> None:
    """Shift + Windows + Spacebar, which is what prompted all of this."""
    assert format_sequence(parse_sequence("Shift+Meta+Space")) == "Meta+Shift+Space"
    assert display_sequence("Meta+Shift+Space") == "Win+Shift+Space"
