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


@pytest.fixture(autouse=True)
def no_real_portal(monkeypatch):
    """Keep every test away from the desktop's screen-sharing dialog.

    Autouse and suite-wide on purpose. "Remember to stub the portal" is a habit, and this was
    already forgotten once in a file whose author had no reason to think it captured anything.
    """
    from app.services.session import manager as manager_module

    monkeypatch.setattr(manager_module, "PortalSession", NoPortalInTests)
