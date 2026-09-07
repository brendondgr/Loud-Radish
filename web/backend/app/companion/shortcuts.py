"""Register global shortcuts with the desktop, and say so when that is not possible (Plan 5).

**Under Wayland an application cannot grab keys itself.** Reading `/dev/input` directly would mean a
keylogger running as the user, which is not a reasonable thing for a transcription tool to install
and is not something this will ever do. So the desktop is asked, and there were three ways to ask.
Two were tried on a real Plasma 6.7 Wayland session and rejected for reasons recorded in
`desktop_entry.py`; the third is what this module does — a `.desktop` entry per action, registered
with `org.kde.KGlobalAccel` as a **service** component whose one action is `_launch`.

The virtue of that route is that it does not depend on this application. KDE writes the binding to
its own configuration, installs the grab straight away, honours it whether or not anything of ours
is running, restores it after a reboot, and shows it in System Settings where it can be changed or
revoked. A shortcut for a tool that records audio *should* be revocable somewhere the tool does not
control.

**A conflicting key is reported, never taken.** `globalShortcutAvailable` is consulted first, and
three of the five shortcuts this application used to ship as defaults turned out to be taken on a
stock desktop — `Meta+Alt+L` by the keyboard layout switcher, `Meta+Alt+R` by Spectacle's screen
recorder, `Meta+Alt+S` by the screen reader. They had never been checked against a real machine.

When no shortcut service is reachable — a different desktop, or a session without one — this **does
not fail silently**. It reports which shortcuts were not registered and prints the exact command to
bind by hand, because a key that does nothing and says nothing is indistinguishable from a broken
application.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .. import branding
from . import desktop_entry, keys

logger = logging.getLogger(__name__)

KGLOBALACCEL_BUS: Final = "org.kde.kglobalaccel"
KGLOBALACCEL_PATH: Final = "/kglobalaccel"
KGLOBALACCEL_IFACE: Final = "org.kde.KGlobalAccel"

COMPONENT: Final = branding.SHORTCUT_COMPONENT
LEGACY_COMPONENT: Final = branding.LEGACY_SHORTCUT_COMPONENT
COMPONENT_LABEL: Final = branding.APP_TITLE

#: Action name → (human label, control-script arguments).
ACTIONS: Final[dict[str, tuple[str, list[str]]]] = {
    "toggle_live": ("Start or stop live transcription", ["toggle", "--mode", "live"]),
    "toggle_recorded": ("Start or stop a recording", ["toggle", "--mode", "recorded"]),
    # Not `toggle`: window capture has three switches that must be answered before anything is
    # captured, so the key opens the sheet rather than starting a recording.
    "arm_window": ("Record a window…", ["arm", "--mode", "window"]),
    "stop": ("Stop recording", ["stop"]),
    "open_app": (f"Open {branding.APP_NAME}", ["arm", "--mode", "live"]),
    "dictate": ("Dictate into the focused window", ["dictate"]),
}

KGLOBALACCEL_COMPONENT_IFACE: Final = "org.kde.kglobalaccel.Component"

#: `KGlobalAccel::SetShortcutFlag`. **2 is load-bearing**: with any other value the shortcut is
#: stored and reported bound, but the component is never marked present and KWin never installs the
#: grab, so the key reaches the focused window instead. Verified by pressing it.
SET_SHORTCUT_FLAGS: Final = 2 | 4


@dataclass(frozen=True)
class Registration:
    """What happened to one shortcut."""

    action: str
    sequence: str
    registered: bool
    reason: str = ""


def service_available() -> bool:
    """Whether a shortcut service this can talk to is running."""
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return False

    try:
        with open_dbus_connection(bus="SESSION") as connection:
            address = DBusAddress(
                object_path=KGLOBALACCEL_PATH,
                bus_name=KGLOBALACCEL_BUS,
                interface=KGLOBALACCEL_IFACE,
            )
            connection.send_and_get_reply(new_method_call(address, "allMainComponents"))
    except Exception:  # noqa: BLE001 - absent, refused, or a different desktop entirely
        return False
    return True


def manual_command(repo_root: Path | str, action: str) -> str:
    """The command to bind by hand, for a desktop this cannot register with.

    Printed rather than hidden: it turns "shortcuts do not work here" into two minutes in the
    desktop's own settings, and it is the same path a user on GNOME or Sway would take anyway.
    """
    arguments = ACTIONS.get(action, ("", []))[1]
    return desktop_entry.command_for(repo_root, arguments)


def _identity(action: str) -> list[str]:
    """The four strings KGlobalAccel identifies one shortcut by.

    The component is the `.desktop` file's name and the action is always `_launch`, which is how
    KDE models "a key that runs a command". That is why there is one entry per action rather than
    one entry for the application: a service component has exactly one launchable action.
    """
    return [
        desktop_entry.entry_name(action),
        "_launch",
        f"{branding.APP_NAME}: {ACTIONS[action][0]}",
        ACTIONS[action][0],
    ]


def _holder(connection, sequence: str) -> str:  # noqa: ANN001
    """Who already owns ``sequence``, in words, or empty if nobody does."""
    from jeepney import DBusAddress, new_method_call

    address = DBusAddress(
        object_path=KGLOBALACCEL_PATH, bus_name=KGLOBALACCEL_BUS, interface=KGLOBALACCEL_IFACE
    )
    chord = [keys.parse_sequence(sequence), 0, 0, 0]
    reply = connection.send_and_get_reply(
        new_method_call(address, "globalShortcutsByKey", "(ai)(i)", ((chord,), (0,)))
    )
    for entry in reply.body[0]:
        # (actionUnique, actionFriendly, componentUnique, componentFriendly, ...)
        if entry[2] in {desktop_entry.entry_name(name) for name in ACTIONS}:
            continue  # our own, from a previous run
        return f"{entry[3] or entry[2]} — {entry[1] or entry[0]}"
    return ""


def register_all(config, repo_root: Path | str) -> list[Registration]:  # noqa: ANN001
    """Install every shortcut's desktop entry and bind its key. Never raises.

    A failure here costs a convenience — the shortcuts — and must not stop the tray icon appearing
    or the server running.
    """
    if not getattr(config, "enabled", True):
        return [
            Registration(action, getattr(config, action, ""), False, "Shortcuts are switched off.")
            for action in ACTIONS
        ]

    try:
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return [
            Registration(
                action, getattr(config, action, ""), False, "The D-Bus client is not installed."
            )
            for action in ACTIONS
        ]

    if not service_available():
        reason = (
            "This desktop has no shortcut service this can register with. "
            "Bind the commands by hand in your desktop's keyboard settings."
        )
        return [
            Registration(action, getattr(config, action, ""), False, reason) for action in ACTIONS
        ]

    # **Every entry is written and the database refreshed before anything is registered.** Doing
    # it per action instead — write one, bind it, write the next — fails for whichever action is
    # handled after the last refresh: KDE will not accept a service registration for a `.desktop`
    # file it has not yet indexed, and it says so by storing nothing rather than by refusing. Found
    # by running it: five of six bound on the first attempt and all six on the second.
    wanted = {action: getattr(config, action, "") for action in ACTIONS}
    for action, sequence in wanted.items():
        if sequence:
            label, arguments = ACTIONS[action]
            desktop_entry.install(action, label, arguments, repo_root)
    desktop_entry.refresh_database()

    results: list[Registration] = []
    try:
        with open_dbus_connection(bus="SESSION") as connection:
            for action, sequence in wanted.items():
                if not sequence:
                    results.append(Registration(action, "", False, "No key set."))
                    continue
                results.append(_register_one(connection, action, sequence))
    except Exception as exc:  # noqa: BLE001 - a broken bus must not stop the tray appearing
        logger.warning("Could not register shortcuts: %s", exc)
        done = {entry.action for entry in results}
        results.extend(
            Registration(action, getattr(config, action, ""), False, f"The bus failed: {exc}")
            for action in ACTIONS
            if action not in done
        )
    return results


def _register_one(connection, action: str, sequence: str) -> Registration:  # noqa: ANN001
    """Bind one key, having first asked whether anyone else already holds it."""
    from jeepney import DBusAddress, new_method_call

    address = DBusAddress(
        object_path=KGLOBALACCEL_PATH, bus_name=KGLOBALACCEL_BUS, interface=KGLOBALACCEL_IFACE
    )
    identity = _identity(action)

    try:
        chord = keys.parse_sequence(sequence)
    except keys.KeyError_ as exc:
        return Registration(action, sequence, False, str(exc))

    holder = _holder(connection, sequence)
    if holder:
        # **Reported, never taken.** Stealing a key another application holds is not ours to do,
        # and three of the defaults this project used to ship collide on a stock desktop.
        return Registration(action, sequence, False, f"Already used by {holder}.")

    try:
        connection.send_and_get_reply(new_method_call(address, "doRegister", "as", (identity,)))
        connection.send_and_get_reply(
            new_method_call(
                address,
                "setShortcutKeys",
                "asa(ai)u",
                (identity, [([chord, 0, 0, 0],)], SET_SHORTCUT_FLAGS),
            )
        )
        # Read back rather than trust. The encoding is the part that is wrong in a way nothing else
        # notices, and this is the one place it can be caught.
        reply = connection.send_and_get_reply(
            new_method_call(address, "shortcutKeys", "as", (identity,))
        )
    except Exception as exc:  # noqa: BLE001
        return Registration(action, sequence, False, f"The desktop refused it: {exc}")

    bound = [row[0][0] for row in reply.body[0] if row[0]]
    if chord not in bound:
        readable = ", ".join(keys.format_sequence(value) for value in bound) or "nothing"
        return Registration(
            action, sequence, False, f"The desktop stored {readable} instead of {sequence}."
        )
    return Registration(action, sequence, True)


def unregister_all() -> list[str]:
    """Remove every binding and entry this application installed. Returns what was removed."""
    removed: list[str] = []
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        address = DBusAddress(
            object_path=KGLOBALACCEL_PATH, bus_name=KGLOBALACCEL_BUS, interface=KGLOBALACCEL_IFACE
        )
        with open_dbus_connection(bus="SESSION") as connection:
            for action in ACTIONS:
                connection.send_and_get_reply(
                    new_method_call(
                        address, "unregister", "ss", (desktop_entry.entry_name(action), "_launch")
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not unregister shortcuts: %s", exc)

    for action in ACTIONS:
        if desktop_entry.remove(action):
            removed.append(desktop_entry.entry_name(action))
    desktop_entry.refresh_database()
    return removed


def describe(results: list[Registration], repo_root: Path | str) -> str:
    """A report a user can act on, for the log and for the settings panel."""
    lines: list[str] = []
    failed = [entry for entry in results if not entry.registered]

    for entry in results:
        mark = "✓" if entry.registered else "·"
        lines.append(f"  {mark} {entry.sequence or '(unset)':<16} {ACTIONS[entry.action][0]}")

    if failed:
        lines.append("")
        lines.append("  Not registered. Bind these by hand in your keyboard settings:")
        lines.append("")
        for entry in failed:
            lines.append(f"      {entry.sequence or '(choose a key)'}")
            lines.append(f"          {manual_command(repo_root, entry.action)}")
    return "\n".join(lines)
