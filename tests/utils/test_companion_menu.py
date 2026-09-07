"""The tray menu's structure, per application state (Plan 5).

Built as data so it is testable without a session bus — which is the part most likely to be wrong.
An item enabled when it should not be is a menu that starts a second recording over the first, and
that is not a mistake a user can undo.
"""

from __future__ import annotations

from app.companion.animation import Snapshot
from app.companion.menu import build
from app.services.session import modes

from app import branding


def ids(items) -> list[str]:
    return [item.id for item in items]


def item(items, name: str):
    return next(entry for entry in items if entry.id == name)


def test_an_unreachable_server_offers_almost_nothing() -> None:
    """Every other item would fail at the moment it was clicked."""
    items = build(Snapshot(reachable=False), listening=True)
    assert "start-live" not in ids(items)
    assert f"{branding.APP_NAME} is not running" in item(items, "status").label
    # Still quittable and still openable — those are the only two that can work.
    assert {"open", "quit"} <= set(ids(items))


def test_idle_offers_all_three_modes() -> None:
    items = build(Snapshot(reachable=True, state=modes.IDLE), listening=True)
    assert {"start-live", "start-recorded", "arm-window"} <= set(ids(items))
    assert "stop" not in ids(items)


def test_recording_offers_only_stop() -> None:
    """Starting a second recording over the first is not a mistake a user can undo."""
    items = build(Snapshot(reachable=True, state=modes.RECORDING), listening=True)
    assert "stop" in ids(items)
    assert "start-live" not in ids(items)


def test_stopping_shows_stop_but_disabled() -> None:
    """Hidden would make the menu jump under the pointer; disabled says why."""
    items = build(Snapshot(reachable=True, state=modes.STOPPING), listening=True)
    assert item(items, "stop").enabled is False


def test_a_transcription_pass_disables_starting_another() -> None:
    items = build(Snapshot(reachable=True, state=modes.PROCESSING), listening=True)
    assert item(items, "start-live").enabled is False


def test_window_capture_arms_rather_than_starting() -> None:
    """Its three options must be answered before anything is captured, and the browser already has
    a sheet for them — a second native dialog would be two implementations of the same toggles."""
    items = build(Snapshot(reachable=True, state=modes.IDLE), listening=True)
    assert item(items, "arm-window").label.endswith("…")


def test_listening_is_a_checkmark_independent_of_recording() -> None:
    """The brief asks for both a disable switch and recording control. Conflating them would mean
    disabling the app killed a recording in progress."""
    recording = build(Snapshot(reachable=True, state=modes.RECORDING), listening=True)
    entry = item(recording, "listening")
    assert entry.kind == "checkmark"
    assert entry.checked is True

    off = build(Snapshot(reachable=True, state=modes.RECORDING), listening=False)
    assert item(off, "listening").checked is False
    assert "stop" in ids(off), "disarming shortcuts must not remove the way to stop recording"


def test_the_status_line_says_what_it_is_doing() -> None:
    cases = {
        modes.IDLE: "Ready",
        modes.RECORDING: "Recording",
        modes.PROCESSING: "Transcribing",
        modes.STOPPING: "Stopping",
        modes.ARMING: "Choosing",
        modes.ERROR: "wrong",
    }
    for state, expected in cases.items():
        items = build(Snapshot(reachable=True, state=state), listening=True)
        assert expected in item(items, "status").label, state


def test_a_background_rewrite_is_mentioned_when_nothing_louder_is() -> None:
    items = build(Snapshot(reachable=True, state=modes.IDLE, running_pass=True), listening=True)
    assert "Tidying" in item(items, "status").label


def test_the_status_line_is_never_clickable() -> None:
    items = build(Snapshot(reachable=True, state=modes.IDLE), listening=True)
    assert item(items, "status").enabled is False


def test_separators_declare_themselves_to_dbus() -> None:
    items = build(Snapshot(reachable=True, state=modes.IDLE), listening=True)
    separator = next(entry for entry in items if entry.kind == "separator")
    assert separator.as_dbus() == {"type": "separator"}


def test_a_checkmark_carries_its_toggle_state() -> None:
    items = build(Snapshot(reachable=True, state=modes.IDLE), listening=True)
    properties = item(items, "listening").as_dbus()
    assert properties["toggle-type"] == "checkmark"
    assert properties["toggle-state"] == 1
