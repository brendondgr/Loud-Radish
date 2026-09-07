"""The clipboard, the paste and the notification, and the order they are tried in (D-048).

Every backend here is a subprocess or a bus call, so these tests fake both. What they are really
pinning is the **selection logic**, because that is where the interesting decisions live:

- an unavailable backend is skipped rather than counted as a failure;
- a failing backend falls through to the next;
- an empty string is refused rather than clearing the user's clipboard;
- and `ydotool` is tried before `wtype`, which is not alphabetical and not obvious.

That last one is the one to protect. Both tools return exit status 0; when they were run one after
the other against the same focused window, `ydotool` pasted and `wtype` did nothing. A backend that
reports success while having no effect is worse than one that fails, and nothing but this ordering
stops the caller believing dictated words were delivered when they were not.
"""

from __future__ import annotations

import subprocess

import pytest
from app.desktop import clipboard, keystroke
from app.desktop.outcome import Outcome


@pytest.fixture
def wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)


@pytest.fixture
def no_tools(monkeypatch):
    """Nothing on the PATH, so each test opts its own tools back in."""
    monkeypatch.setattr(clipboard.shutil, "which", lambda _name: None)
    monkeypatch.setattr(keystroke.shutil, "which", lambda _name: None)


def _present(*names: str):
    return lambda name: f"/usr/bin/{name}" if name in names else None


# -- the clipboard ---------------------------------------------------------------------------


def test_an_empty_string_is_refused_rather_than_clearing_the_clipboard(no_tools) -> None:
    """**Destroying what the user had copied is worse than doing nothing.** A dictation that
    produced no words must not also take away the paragraph they were about to paste."""
    result = clipboard.copy("")

    assert not result.ok
    assert "nothing to copy" in result.detail


def test_wl_copy_is_used_when_it_is_there(monkeypatch, wayland, no_tools) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", _present("wl-copy"))
    seen = {}
    monkeypatch.setattr(
        clipboard.subprocess, "run", lambda cmd, **kw: seen.update(cmd=cmd, input=kw.get("input"))
    )

    result = clipboard.copy("hello")

    assert result.ok
    assert result.backend == "wl-copy"
    assert seen["input"] == b"hello"


def test_an_absent_tool_is_not_counted_as_a_failure(monkeypatch, wayland, no_tools) -> None:
    """Reporting "tried wl-copy, klipper, xclip" when two are not installed is misleading."""
    monkeypatch.setattr(clipboard, "_via_klipper", lambda _text: Outcome(True, "klipper"))

    result = clipboard.copy("hello")

    assert result.attempts == ["klipper"]


def test_a_failing_backend_falls_through_to_the_next(monkeypatch, wayland, no_tools) -> None:
    monkeypatch.setattr(clipboard, "_via_wl_copy", lambda _t: Outcome(False, "wl-copy", "broke"))
    monkeypatch.setattr(clipboard, "_via_klipper", lambda _t: Outcome(True, "klipper"))

    result = clipboard.copy("hello")

    assert result.ok
    assert result.backend == "klipper"
    assert result.attempts == ["wl-copy", "klipper"]


def test_when_nothing_works_the_report_names_what_was_tried(monkeypatch, wayland, no_tools) -> None:
    monkeypatch.setattr(clipboard, "_via_wl_copy", lambda _t: Outcome(False, "wl-copy", "broke"))
    monkeypatch.setattr(clipboard, "_via_klipper", lambda _t: Outcome(False, "klipper", "absent"))

    result = clipboard.copy("hello")

    assert not result.ok
    assert "wl-copy" in result.describe()
    assert "klipper" in result.describe()


def test_a_crashing_tool_is_an_answer_not_an_exception(monkeypatch, wayland, no_tools) -> None:
    monkeypatch.setattr(clipboard.shutil, "which", _present("wl-copy"))

    def explode(*_a, **_k):
        raise subprocess.CalledProcessError(1, "wl-copy")

    monkeypatch.setattr(clipboard.subprocess, "run", explode)
    # The other two are stubbed out as well, or this test reaches the developer's real Klipper —
    # which is on the session bus here, would succeed, and would make the assertion pass for the
    # wrong reason while writing to their actual clipboard.
    monkeypatch.setattr(clipboard, "_via_klipper", lambda _t: Outcome(False, "klipper", "stubbed"))
    monkeypatch.setattr(clipboard, "_via_xclip", lambda _t: None)

    assert clipboard.copy("hello").ok is False


# -- the paste -------------------------------------------------------------------------------


def test_ydotool_is_tried_before_wtype() -> None:
    """**The load-bearing ordering.** Both return 0; only one of them actually pastes."""
    assert list(keystroke.BACKENDS) == ["ydotool", "wtype"]


def test_the_paste_uses_ydotool_when_both_are_present(monkeypatch, wayland, no_tools) -> None:
    monkeypatch.setattr(keystroke.shutil, "which", _present("ydotool", "wtype"))
    monkeypatch.setattr(keystroke.subprocess, "run", lambda *_a, **_k: None)

    assert keystroke.paste().backend == "ydotool"


def test_wtype_is_used_only_when_there_is_no_ydotool(monkeypatch, wayland, no_tools) -> None:
    monkeypatch.setattr(keystroke.shutil, "which", _present("wtype"))
    monkeypatch.setattr(keystroke.subprocess, "run", lambda *_a, **_k: None)

    assert keystroke.paste().backend == "wtype"


def test_the_chord_becomes_press_and_release_in_reverse(monkeypatch, wayland, no_tools) -> None:
    """A modifier released before the key it modifies is a different keystroke."""
    monkeypatch.setattr(keystroke.shutil, "which", _present("ydotool"))
    seen = {}
    monkeypatch.setattr(keystroke.subprocess, "run", lambda cmd, **_k: seen.update(cmd=cmd))

    keystroke.paste("ctrl+shift+v")

    codes = seen["cmd"][seen["cmd"].index("-d") + 2 :]
    assert codes == ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"]


def test_the_terminal_chord_is_expressible(monkeypatch, wayland, no_tools) -> None:
    """In a terminal, Ctrl+V quotes the next character and paste is Ctrl+Shift+V. There is no way
    to ask the compositor what kind of window has focus, so this is a setting, not a detection."""
    monkeypatch.setattr(keystroke.shutil, "which", _present("ydotool"))
    monkeypatch.setattr(keystroke.subprocess, "run", lambda *_a, **_k: None)

    assert keystroke.paste("ctrl+shift+v").ok


def test_a_key_ydotool_cannot_translate_is_refused(monkeypatch, wayland, no_tools) -> None:
    monkeypatch.setattr(keystroke.shutil, "which", _present("ydotool"))

    result = keystroke.paste("ctrl+quokka")

    assert not result.ok


def test_a_machine_with_neither_tool_says_so(monkeypatch, wayland, no_tools) -> None:
    result = keystroke.paste()

    assert not result.ok
    assert "type into another window" in result.detail
    assert keystroke.available() is False


# -- the outcome type ------------------------------------------------------------------------


def test_an_outcome_reads_as_a_boolean() -> None:
    assert bool(Outcome(True, "wl-copy"))
    assert not bool(Outcome(False))


def test_a_successful_outcome_describes_itself_without_the_attempts() -> None:
    assert Outcome(True, "ydotool", "ctrl+v").describe() == "ydotool: ctrl+v"
