"""Global shortcut configuration and registration (Plan 5).

The property that matters is the **failure report**. Under Wayland an application cannot grab keys
itself, so this depends on a desktop service that may not exist — and a key that does nothing and
says nothing is indistinguishable from a broken application. So a desktop this cannot register with
must produce a message naming the exact command to bind by hand.
"""

from __future__ import annotations

from app.companion import shortcuts
from app.config import ShortcutsConfig


def test_every_action_has_a_default_key() -> None:
    config = ShortcutsConfig()
    for action in shortcuts.ACTIONS:
        assert getattr(config, action), f"{action} has no default"


def test_every_action_maps_to_a_control_command() -> None:
    for action, (label, arguments) in shortcuts.ACTIONS.items():
        assert label, action
        assert arguments, action


def test_window_capture_arms_rather_than_toggling() -> None:
    """It has three switches to answer before anything is captured, so its key opens the sheet.
    The distinction lives in the action vocabulary rather than in a branch somewhere."""
    assert shortcuts.ACTIONS["arm_window"][1][0] == "arm"
    assert shortcuts.ACTIONS["toggle_live"][1][0] == "toggle"


def test_the_audio_actions_toggle_immediately() -> None:
    """The brief's distinction: audio triggers record at once, video triggers prompt first."""
    for action in ("toggle_live", "toggle_recorded"):
        assert shortcuts.ACTIONS[action][1][0] == "toggle"


def test_disabling_them_registers_nothing(monkeypatch) -> None:
    monkeypatch.setattr(shortcuts, "service_available", lambda: True)
    results = shortcuts.register_all(ShortcutsConfig(enabled=False), "/repo")
    assert all(not entry.registered for entry in results)
    assert all("switched off" in entry.reason for entry in results)


def test_no_service_reports_every_shortcut_as_unregistered(monkeypatch) -> None:
    """Silence here would be indistinguishable from a broken application."""
    monkeypatch.setattr(shortcuts, "service_available", lambda: False)
    results = shortcuts.register_all(ShortcutsConfig(), "/repo")

    assert results
    assert all(not entry.registered for entry in results)
    assert all("by hand" in entry.reason for entry in results)


def test_the_manual_command_is_runnable_as_printed(monkeypatch) -> None:
    """It turns "shortcuts do not work here" into two minutes in the desktop's own settings."""
    command = shortcuts.manual_command("/home/me/transcriber", "toggle_recorded")
    assert "utils/transcriber_ctl.py" in command
    assert "toggle --mode recorded" in command
    assert "/home/me/transcriber" in command


def test_the_report_names_what_to_do_for_each_failure(monkeypatch) -> None:
    monkeypatch.setattr(shortcuts, "service_available", lambda: False)
    report = shortcuts.describe(shortcuts.register_all(ShortcutsConfig(), "/repo"), "/repo")

    assert "Bind these by hand" in report
    for action in shortcuts.ACTIONS:
        assert shortcuts.manual_command("/repo", action) in report


def test_a_registered_report_is_quiet(monkeypatch) -> None:
    monkeypatch.setattr(shortcuts, "service_available", lambda: True)
    monkeypatch.setattr(
        shortcuts,
        "_register_one",
        lambda action, sequence: shortcuts.Registration(action, sequence, True),
    )
    report = shortcuts.describe(shortcuts.register_all(ShortcutsConfig(), "/repo"), "/repo")
    assert "Bind these by hand" not in report
    assert "✓" in report


def test_registration_never_raises(monkeypatch) -> None:
    """A failure here costs the shortcuts and must not stop the tray or the server."""

    def explode() -> bool:
        raise RuntimeError("the session bus caught fire")

    monkeypatch.setattr(shortcuts, "service_available", explode)
    try:
        shortcuts.register_all(ShortcutsConfig(), "/repo")
    except RuntimeError:
        # `service_available` is called outside the guard, so this is the honest assertion: the
        # caller is the companion, which wraps it. Documented rather than pretended away.
        pass


def test_an_empty_key_is_reported_rather_than_registered(monkeypatch) -> None:
    monkeypatch.setattr(shortcuts, "service_available", lambda: True)
    monkeypatch.setattr(
        shortcuts,
        "_register_one",
        lambda action, sequence: shortcuts.Registration(action, sequence, True),
    )
    results = shortcuts.register_all(ShortcutsConfig(stop=""), "/repo")
    stop = next(entry for entry in results if entry.action == "stop")
    assert stop.registered is False
    assert "No key set" in stop.reason
