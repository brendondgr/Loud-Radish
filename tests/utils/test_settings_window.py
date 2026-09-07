"""The settings window's arithmetic, without a main loop (D-051).

Tk cannot be driven from a test, so everything worth testing lives in `settings_form.py` — and what
is worth testing is precisely the part that was wrong the last time this repository turned a
keypress into a stored string: `Ctrl+Alt+Shift+F9` became `Ctrl+Alt+Shift+8` because a key's
`0x01000000` prefix was dropped, and nothing in the round trip contradicted it.

**One trap is recorded here rather than papered over.** A synthetic `event_generate` reports a
different modifier mask than a real press — a scripted `Control-Alt-r` arrived with state `0x20004`
where a real one carries `0x0C`. So these tests use the masks a real X server sends, and any future
attempt to drive Tk with `event_generate` should not be trusted to reproduce them.
"""

from __future__ import annotations

import pytest
from app.companion import keys, settings_form
from app.companion.settings_form import ALT, CONTROL, META, SHIFT, NotAShortcut, chord_from_event

# -- a keypress becomes a sequence ------------------------------------------------------------


def test_a_modified_letter_becomes_a_sequence() -> None:
    assert chord_from_event("d", META | ALT) == "Meta+Alt+D"


def test_the_modifier_order_matches_what_kde_writes() -> None:
    """So the string in our config file and the string in `kglobalshortcutsrc` are the same."""
    assert chord_from_event("f9", CONTROL | ALT | SHIFT) == "Ctrl+Alt+Shift+F9"


def test_a_function_key_survives_the_trip_to_kglobalaccels_integer() -> None:
    """**The bug this whole family of tests exists for.** Without the `0x01000000` prefix this
    sequence binds the digit 8, and every check but the keypress itself still passes."""
    sequence = chord_from_event("f9", CONTROL | ALT | SHIFT)

    assert keys.parse_sequence(sequence) == 0x0F000038


@pytest.mark.parametrize(
    ("keysym", "expected"),
    [
        ("prior", "Meta+PgUp"),
        ("next", "Meta+PgDown"),
        ("space", "Meta+Space"),
        ("Return", "Meta+Return"),
        ("BackSpace", "Meta+Backspace"),
        ("Delete", "Meta+Delete"),
        ("Left", "Meta+Left"),
    ],
)
def test_the_named_keys_tk_reports_are_translated(keysym, expected) -> None:
    """Tk's names are not KDE's: `prior` is Page Up and `next` is Page Down."""
    assert chord_from_event(keysym, META) == expected


def test_every_captured_sequence_can_be_encoded() -> None:
    """A window that accepts a shortcut which cannot then be registered is worse than one that
    refuses it, because the refusal happens later and somewhere else."""
    for keysym in ("a", "z", "1", "f1", "f12", "space", "Delete", "Home"):
        assert keys.is_readable(chord_from_event(keysym, META | ALT))


# -- what is refused ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "keysym", ["Shift_L", "Control_R", "Alt_L", "Super_L", "Caps_Lock", "Meta_R"]
)
def test_a_modifier_on_its_own_is_not_a_shortcut(keysym) -> None:
    with pytest.raises(NotAShortcut, match="press a key as well"):
        chord_from_event(keysym, CONTROL)


def test_a_bare_letter_is_refused() -> None:
    """**A global shortcut with no modifier takes that key away from every other application.**
    Someone capturing "D" almost certainly meant to hold something down as well."""
    with pytest.raises(NotAShortcut, match="every other application"):
        chord_from_event("d", 0)


def test_a_bare_function_key_is_allowed() -> None:
    """F-keys are the exception: nobody types F9 into a document."""
    assert chord_from_event("f9", 0) == "F9"


def test_a_key_that_cannot_be_encoded_says_so() -> None:
    with pytest.raises(NotAShortcut):
        chord_from_event("Hangul_Jeonja", META)


def test_nothing_pressed_is_not_a_shortcut() -> None:
    with pytest.raises(NotAShortcut):
        chord_from_event("", META)


def test_caps_lock_does_not_change_the_shortcut() -> None:
    """Otherwise the same keys captured twice produce two different sequences."""
    caps, num = 0x0002, 0x0010

    assert chord_from_event("d", META | ALT | caps | num) == "Meta+Alt+D"


# -- conflicts ---------------------------------------------------------------------------------


def test_our_own_duplicate_is_named_as_ours() -> None:
    """The desktop would report this as *us* holding the key, which reads like a bug rather than
    the duplicate it is — so it is checked here first, and without touching the bus."""
    current = {"dictate": "Meta+Alt+D", "stop": "Meta+Alt+X"}

    clash = settings_form.conflict_for("Meta+Alt+D", "stop", current, holder=lambda _s: "")

    assert "your own dictate shortcut" == clash


def test_rebinding_a_shortcut_to_the_key_it_already_has_is_not_a_conflict() -> None:
    current = {"dictate": "Meta+Alt+D"}

    assert settings_form.conflict_for("Meta+Alt+D", "dictate", current, lambda _s: "") == ""


def test_a_key_another_application_holds_is_reported() -> None:
    clash = settings_form.conflict_for(
        "Meta+Alt+L", "dictate", {}, holder=lambda _s: "KDE Keyboard Layout Switcher"
    )

    assert clash == "KDE Keyboard Layout Switcher"


def test_a_desktop_that_cannot_be_asked_is_not_a_conflict() -> None:
    """A machine with no shortcut service must not refuse every key the user picks."""
    assert settings_form.conflict_for("Meta+Alt+D", "dictate", {}, holder=None) == ""


# -- what is sent to the server ------------------------------------------------------------------


def test_the_payload_is_what_the_config_route_expects() -> None:
    """The window owns no settings. The backend owns configuration and this writes changes back —
    a native window is not the exception to that rule just because it is not a browser."""
    assert settings_form.patch_payload({"shortcuts.dictate": "Meta+Alt+D"}) == {
        "changes": {"shortcuts.dictate": "Meta+Alt+D"}
    }


def test_unreadable_or_empty_shortcuts_are_not_sent() -> None:
    edits = settings_form.shortcut_edits(
        {"dictate": "Meta+Alt+D", "stop": "", "open_app": "Hyper+Nonsense"}
    )

    assert edits == {"shortcuts.dictate": "Meta+Alt+D"}


# -- the window's own wiring, without opening it ---------------------------------------------


def test_every_offered_shortcut_is_a_real_action() -> None:
    from app.companion.settings_window import SHORTCUT_ORDER
    from app.companion.shortcuts import ACTIONS

    assert set(SHORTCUT_ORDER) == set(ACTIONS), "the window and the registrar disagree"


def test_dictation_is_offered_first() -> None:
    """It is the one pressed dozens of times a day."""
    from app.companion.settings_window import SHORTCUT_ORDER

    assert SHORTCUT_ORDER[0] == "dictate"
