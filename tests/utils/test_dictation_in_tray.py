"""The tray icon while a dictation is running (D-059).

**A dictation has no window.** It is started by a keystroke, with nothing on screen, and the only
thing that can say the microphone is open is the icon in the panel. It did not: the icon is driven
from the *session* state, and a dictation is deliberately not a session (D-049), so dictating looked
exactly like sitting idle. Reported as "there's no indication as to whether or not it's running …
it is hard to tell when it is on or off".

The same report named a second thing: every keypress put a loading indicator on screen, because the
`.desktop` entry the shortcut launches did not say `StartupNotify=false` and KDE assumed a window
was coming. That is asserted here too, next to the fault it belongs with.
"""

from __future__ import annotations

import pytest
from app.companion.animation import Snapshot
from app.companion.menu import build
from app.companion.visual_states import LIVE, TRANSCRIBING, VISUALS, visual_for


def dictating(state: str) -> Snapshot:
    return Snapshot(reachable=True, mode="live", state="idle", dictation=state)


# -- the picture ---------------------------------------------------------------------------------


def test_an_open_microphone_looks_like_listening() -> None:
    """The whole complaint: while the microphone is open the icon must not look idle."""
    assert dictating("recording").visual() is VISUALS[LIVE]
    assert dictating("recording").visual() is not VISUALS["idle"]


@pytest.mark.parametrize("state", ["transcribing", "tidying", "delivering"])
def test_everything_after_the_microphone_closes_looks_like_transcribing(state) -> None:
    """Because that is what is happening — including the tidy pass and the paste."""
    assert dictating(state).visual() is VISUALS[TRANSCRIBING]


def test_an_idle_dictation_service_leaves_the_icon_alone() -> None:
    assert Snapshot(reachable=True, state="idle").visual() is VISUALS["idle"]


def test_a_fault_still_wins() -> None:
    """A dictation running does not hide something being wrong."""
    assert visual_for("live", "error", dictation="recording") is VISUALS["fault"]


def test_an_unreachable_server_still_wins() -> None:
    """The companion cannot know a dictation is running if it cannot reach the server, but if it
    somehow held a stale one, "the application is gone" is the more important thing to show."""
    stale = Snapshot(reachable=False, dictation="recording")

    assert stale.visual() is VISUALS["server-down"]


def test_dictation_outranks_a_background_rewrite() -> None:
    assert visual_for("live", "idle", running_pass=True, dictation="recording") is VISUALS[LIVE]


# -- the words -----------------------------------------------------------------------------------


def test_the_menu_says_which_key_ends_it() -> None:
    """Not everyone reads icons, and the one thing someone needs mid-dictation is how to stop."""
    [status, *_] = build(dictating("recording"), listening=True)

    assert "Dictating" in status.label
    assert "again" in status.label
    assert status.enabled is False


def test_the_menu_says_when_it_is_working_on_what_was_said() -> None:
    [status, *_] = build(dictating("tidying"), listening=True)

    assert "Writing down" in status.label


def test_an_idle_menu_is_unchanged() -> None:
    [status, *_] = build(Snapshot(reachable=True, state="idle"), listening=True)

    assert status.label == "Ready"


# -- the loading indicator on every keypress ------------------------------------------------------


def test_the_shortcut_entries_ask_the_desktop_not_to_show_launch_feedback(tmp_path) -> None:
    """**The second half of the same report.** KDE assumes a `.desktop` application is about to
    open a window, so it shows a task-manager entry and a spinner and waits. These entries open no
    window: they post to a loopback API and exit in 70 ms."""
    from app.companion import desktop_entry

    path = desktop_entry.install("dictate", "Dictate", ["dictate"], tmp_path)
    body = path.read_text(encoding="utf-8")

    assert "StartupNotify=false" in body
    assert "X-KDE-StartupNotify=false" in body
