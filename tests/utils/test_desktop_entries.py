"""The `.desktop` files a shortcut is bound to (D-047).

KDE binds a key to a *service*, and a service is a `.desktop` file. That is why there is one file
per action rather than one for the application: a service component's shortcut is always the action
`_launch`, so two shortcuts sharing an entry would be two names for one key.

**These tests must never write to the developer's real applications directory.** An autouse fixture
in `tests/conftest.py` redirects `XDG_DATA_HOME`, and it exists because the first run of the code
under test left `loud-radish-toggle-live.desktop` in the developer's menu.
"""

from __future__ import annotations

import pytest
from app.companion import desktop_entry, shortcuts

from app import branding


@pytest.fixture
def repo(tmp_path):
    return tmp_path / "checkout"


# -- where files go --------------------------------------------------------------------------


def test_the_directory_follows_xdg_data_home(tmp_path) -> None:
    """The redirect the suite's guard relies on. If this stops being read, the guard stops working
    and the next test run lands in the developer's menu again."""
    assert desktop_entry.applications_dir() == tmp_path / "xdg-data" / "applications"


def test_an_install_lands_inside_the_redirected_directory(repo, tmp_path) -> None:
    """**Not "the real home has no such file".** The first version of this test asserted exactly
    that, and failed the moment the developer registered the shortcuts for real — the same mistake
    as asserting on the developer's audio: a claim about a machine the test does not own. What the
    guard actually promises is that a write goes to the redirected directory, so that is what is
    checked."""
    written = desktop_entry.install("toggle_live", "Live", ["toggle"], repo)

    assert written.parent == desktop_entry.applications_dir()
    assert tmp_path in written.parents, "the write escaped the per-test directory"


def test_each_action_gets_its_own_file(repo) -> None:
    for action in shortcuts.ACTIONS:
        desktop_entry.install(action, "x", ["stop"], repo)

    written = sorted(path.name for path in desktop_entry.applications_dir().iterdir())
    assert len(written) == len(shortcuts.ACTIONS)
    assert len(set(written)) == len(written), "two actions share a file, so they share a key"


def test_the_name_is_derived_from_the_action(repo) -> None:
    assert desktop_entry.entry_name("toggle_live") == f"{branding.APP_SLUG}-toggle-live.desktop"
    assert desktop_entry.entry_name("dictate") == f"{branding.APP_SLUG}-dictate.desktop"


# -- what is in them -------------------------------------------------------------------------


def test_the_exec_line_is_absolute(repo) -> None:
    """**The desktop launches this, not a shell in this checkout.** A relative path, or a bare
    `uv`, resolves against whatever PATH and working directory the desktop happens to have."""
    path = desktop_entry.install("dictate", "Dictate", ["dictate"], repo)

    exec_line = next(
        line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("Exec=")
    )
    interpreter, script, *arguments = exec_line.removeprefix("Exec=").split()
    assert interpreter.startswith("/")
    assert script.startswith(str(repo))
    assert arguments == ["dictate"]


def test_the_entry_is_hidden_from_the_application_menu(repo) -> None:
    """Six entries called "Loud Radish: Stop recording" in the launcher is clutter nobody asked
    for. They are shortcut targets, not applications."""
    path = desktop_entry.install("stop", "Stop recording", ["stop"], repo)

    assert "NoDisplay=true" in path.read_text(encoding="utf-8")


def test_reinstalling_replaces_a_stale_command(repo, tmp_path) -> None:
    """The checkout can move. A stale `Exec=` is a shortcut that fails silently."""
    desktop_entry.install("stop", "Stop", ["stop"], tmp_path / "old-place")

    path = desktop_entry.install("stop", "Stop", ["stop"], repo)

    body = path.read_text(encoding="utf-8")
    assert str(repo) in body
    assert "old-place" not in body


# -- removal ---------------------------------------------------------------------------------


def test_removing_reports_whether_there_was_anything_to_remove(repo) -> None:
    desktop_entry.install("stop", "Stop", ["stop"], repo)

    assert desktop_entry.remove("stop") is True
    assert desktop_entry.remove("stop") is False


# -- the identity KGlobalAccel is given ------------------------------------------------------


def test_the_component_is_the_desktop_file_and_the_action_is_launch() -> None:
    """The shape KDE requires for a command shortcut, and the reason for one file per action."""
    component, action, _friendly, _label = shortcuts._identity("dictate")

    assert component == desktop_entry.entry_name("dictate")
    assert action == "_launch"


def test_every_action_has_a_readable_default_key() -> None:
    """A default that cannot be encoded is a shortcut that can never be registered."""
    from app.companion import keys
    from app.config.schema import ShortcutsConfig

    config = ShortcutsConfig()
    for action in shortcuts.ACTIONS:
        sequence = getattr(config, action)
        assert sequence, f"{action} ships with no key"
        assert keys.is_readable(sequence), f"{action}'s default {sequence!r} cannot be encoded"


def test_dictation_has_an_action_and_a_command() -> None:
    label, arguments = shortcuts.ACTIONS["dictate"]

    assert arguments == ["dictate"]
    assert "ictat" in label
