"""Suite-wide guards.

## No test may reach the desktop's screen-sharing portal

`tests/api/test_session_modes.py` starts a `window` session through the real HTTP route, and on a
KDE Wayland desktop that did exactly what the application is supposed to do: it opened
`org.freedesktop.portal.ScreenCast` and put a **"choose a window to share" dialog on the developer's
screen**. Five such tests, five dialogs, each blocking the suite for the full 120-second portal
timeout — which is why a 70-second suite became an eleven-minute one the moment nobody was sitting
there dismissing them.

The failure hid well, and it is worth naming how. `PortalSession._connect` turns *any* transport
failure into `PortalUnavailable`, by design — a machine without a session bus must not crash the
application. So an earlier attempt to find the culprit, by making `open_dbus_connection` raise, was
swallowed by that same guard and reported no offenders. What finally showed it was `--durations`:
five tests at **exactly 120.2 seconds**, which is not a slow test, it is a timeout.

The double below therefore refuses rather than grants. Refusing exercises the designed degradation —
a portal failure costs the video and keeps the talk (D-022) — so the routes under test still take
the path they are there to check, while nothing reaches the compositor. Tests that need a *granted*
portal, like `tests/transcription/test_window_session.py`, patch the same name with a granting
double of their own; a later `monkeypatch.setattr` in a test wins over this one.

## No test may create a capture sink in the developer's audio graph

The same lesson, learned again a day later and more expensively. `_open_application_tap` was never
stubbed, so any test that opened a `window` session's audio ran `pactl load-module module-null-sink`
against the **live PipeWire daemon**, linked whatever the developer happened to be playing into it,
and never closed it — the session's teardown, which is what closes a tap, does not run in those
tests. Measured: five tests in `test_window_audio.py` leaked **three sinks per run**.

That was not merely untidy. Every one was called `transcriber-tap`, PipeWire does not uniquify node
names, and once several exist `pw-link` and `pw-record` can resolve that name to *different nodes* —
so the recorder captures a sink nothing is linked into. Every window recording made that afternoon
came back as bit-exact digital silence, and the cause was the test suite that reported them green.

A unique name per tap now makes the silence impossible; this makes the leak impossible, which is
the fault underneath it. The double below records what was asked of it and touches nothing.
"""

from __future__ import annotations

import pytest


class NoPortalInTests:
    """Stands in for the compositor's picker, and always declines to be reached.

    Raising :class:`PortalUnavailable` rather than ``PortalDeclined`` is deliberate: *declined*
    means the user said no and ends the whole session, while *unavailable* means this machine
    cannot share a window, which costs the video and keeps the recording. The second is what a
    test environment actually is.
    """

    def __init__(self, *, cursor_mode: str = "hidden", restore_token: str = "") -> None:
        self.cursor_mode = cursor_mode
        self.restore_token = restore_token

    def open(self):  # noqa: ANN201 - the real signature returns a WindowStream it never reaches
        from app.services.capture import PortalUnavailable

        raise PortalUnavailable(
            "The screen-sharing portal is not reachable from the test suite. A test that needs a "
            "granted window must patch app.services.session.manager.PortalSession itself."
        )

    def close(self) -> None:
        """Nothing was opened, so there is nothing to close."""


class NoTapInTests:
    """Stands in for the application audio tap, and never reaches PipeWire.

    Behaves like a tap that opened and linked successfully, because that is the path the session
    tests are there to exercise. It creates no sink, so there is nothing to leak when the teardown
    that would have closed it never runs.
    """

    def __init__(self, sink_name: str = "") -> None:
        self.sink_name = sink_name or "transcriber-tap-in-tests"
        self._open = False

    @property
    def monitor(self) -> str:
        return f"{self.sink_name}.monitor"

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def link(self, _stream) -> int:
        return 2

    def link_all(self, streams) -> int:
        return 2 * len(list(streams))

    def close(self) -> None:
        self._open = False


@pytest.fixture(autouse=True)
def no_real_portal(monkeypatch):
    """Keep every test away from the desktop's screen-sharing dialog.

    Autouse and suite-wide on purpose. "Remember to stub the portal" is a habit, and this was
    already forgotten once in a file whose author had no reason to think it captured anything.
    """
    from app.services.session import manager as manager_module

    monkeypatch.setattr(manager_module, "PortalSession", NoPortalInTests)


@pytest.fixture(autouse=True)
def no_real_audio_tap(monkeypatch):
    """Keep every test out of the developer's PipeWire graph.

    Patched on the *manager* rather than in `app.services.audio.tap`, so the tests in
    `test_audio_tap.py` that deliberately exercise the real graph — and clean up after themselves —
    still do. Also stubs the probe: with no real sink there is nothing to listen to, and a probe
    that heard nothing would refuse every window session in the suite.
    """
    from app.services.session import manager as manager_module

    monkeypatch.setattr(manager_module, "ApplicationTap", NoTapInTests)
    monkeypatch.setattr(manager_module, "carries_audio", lambda *_a, **_k: True)
