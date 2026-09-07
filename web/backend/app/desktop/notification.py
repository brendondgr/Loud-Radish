"""A desktop notification, so a dictation that fails says so where the user is looking.

Named `notification` rather than `notify` for the same reason `keystroke.py` is not `paste.py`: the
package exports the function, and a module and a function of the same name shadow each other in a
way that surfaces as `'function' object has no attribute 'notify'`.

There was no notification code anywhere in this repository. The in-browser banner system
(`components/banners.js`) is the only way the application has ever had of telling anyone anything —
and a dictation is precisely the feature where **nobody is looking at the browser**. The words are
meant to appear in the window they are typing in; if they do not, a banner on a tab behind three
other windows is not a message, it is a message nobody receives.

`org.freedesktop.Notifications` over the session bus, through the same `jeepney` connection pattern
`tray.py` already uses. No new dependency. Never raises: a notification that cannot be sent must
not be the thing that breaks the feature it was reporting on.
"""

from __future__ import annotations

import logging

from .. import branding
from .outcome import Outcome

logger = logging.getLogger(__name__)

BUS = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
IFACE = "org.freedesktop.Notifications"

#: `NotificationClosed` reasons are not consumed, so the id is only ever used for replacement.
URGENCY_LOW, URGENCY_NORMAL, URGENCY_CRITICAL = 0, 1, 2

#: How long a notification stays up, in milliseconds. -1 leaves it to the desktop's own setting,
#: which is the right default: this application does not know how long the user needs.
DEFAULT_TIMEOUT_MS = -1


def notify(
    summary: str,
    body: str = "",
    *,
    urgency: int = URGENCY_NORMAL,
    replaces: int = 0,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> Outcome:
    """Show one notification. Returns its id in ``detail`` so it can be replaced.

    Replacing rather than stacking matters for dictation: "listening", "transcribing", "pasted" are
    three states of one action, and three notifications for one dictation is noise.
    """
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return Outcome(False, detail="the D-Bus client is not installed")

    try:
        with open_dbus_connection(bus="SESSION") as connection:
            address = DBusAddress(object_path=PATH, bus_name=BUS, interface=IFACE)
            reply = connection.send_and_get_reply(
                new_method_call(
                    address,
                    "Notify",
                    "susssasa{sv}i",
                    (
                        branding.APP_NAME,
                        replaces,
                        branding.APP_SLUG,
                        summary,
                        body,
                        [],
                        {"urgency": ("y", urgency)},
                        timeout_ms,
                    ),
                )
            )
    except Exception as exc:  # noqa: BLE001 - no daemon, no bus, or a desktop without either
        logger.debug("Could not notify: %s", exc)
        return Outcome(False, detail=str(exc))

    return Outcome(True, "freedesktop", str(reply.body[0]))


def available() -> bool:
    """Whether a notification daemon is on the bus."""
    return bool(notify.__module__) and _daemon_present()


def _daemon_present() -> bool:
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        with open_dbus_connection(bus="SESSION") as connection:
            address = DBusAddress(object_path=PATH, bus_name=BUS, interface=IFACE)
            connection.send_and_get_reply(new_method_call(address, "GetServerInformation"))
    except Exception:  # noqa: BLE001
        return False
    return True
