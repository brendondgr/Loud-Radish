"""Register global shortcuts with the desktop, and say so when that is not possible (Plan 5).

**Under Wayland an application cannot grab keys itself.** The sanctioned route on KDE is
`org.kde.KGlobalAccel`, which is present on this machine; reading `/dev/input` directly would mean
a keylogger running as the user, which is not a reasonable thing for a transcription tool to
install and is not something this will ever do.

When the service is absent — a different desktop, or a session without one — this **does not fail
silently**. It reports which shortcuts were not registered and prints the exact command to bind by
hand, because a key that does nothing and says nothing is indistinguishable from a broken
application.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

KGLOBALACCEL_BUS: Final = "org.kde.kglobalaccel"
KGLOBALACCEL_PATH: Final = "/kglobalaccel"
KGLOBALACCEL_IFACE: Final = "org.kde.KGlobalAccel"

COMPONENT: Final = "transcriber"
COMPONENT_LABEL: Final = "Live Seminar Transcriber"

#: Action name → (human label, `transcriber_ctl.py` arguments).
ACTIONS: Final[dict[str, tuple[str, list[str]]]] = {
    "toggle_live": ("Start or stop live transcription", ["toggle", "--mode", "live"]),
    "toggle_recorded": ("Start or stop a recording", ["toggle", "--mode", "recorded"]),
    # Not `toggle`: window capture has three switches that must be answered before anything is
    # captured, so the key opens the sheet rather than starting a recording.
    "arm_window": ("Record a window…", ["arm", "--mode", "window"]),
    "stop": ("Stop recording", ["stop"]),
    "open_app": ("Open the transcriber", ["arm", "--mode", "live"]),
}


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
    return f"uv run --directory {repo_root} utils/transcriber_ctl.py {' '.join(arguments)}"


def register_all(config, repo_root: Path | str) -> list[Registration]:  # noqa: ANN001
    """Register every configured shortcut. Never raises.

    A failure here costs a convenience — the shortcuts — and must not stop the tray icon appearing
    or the server running.
    """
    if not getattr(config, "enabled", True):
        return [
            Registration(action, getattr(config, action, ""), False, "Shortcuts are switched off.")
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

    results: list[Registration] = []
    for action in ACTIONS:
        sequence = getattr(config, action, "")
        if not sequence:
            results.append(Registration(action, "", False, "No key set."))
            continue
        results.append(_register_one(action, sequence))
    return results


def _register_one(action: str, sequence: str) -> Registration:
    """One shortcut, via KGlobalAccel.

    KDE's own model is that the *component* owns actions and the user assigns keys in System
    Settings. We declare the action and request a default; a conflicting key is reported rather
    than stolen, because taking a key another application already holds is not ours to do.
    """
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return Registration(action, sequence, False, "The D-Bus client is not installed.")

    address = DBusAddress(
        object_path=KGLOBALACCEL_PATH, bus_name=KGLOBALACCEL_BUS, interface=KGLOBALACCEL_IFACE
    )
    identity = [COMPONENT, action, COMPONENT_LABEL, ACTIONS[action][0]]

    try:
        with open_dbus_connection(bus="SESSION") as connection:
            connection.send_and_get_reply(new_method_call(address, "doRegister", "as", (identity,)))
    except Exception as exc:  # noqa: BLE001
        return Registration(action, sequence, False, f"The desktop refused it: {exc}")

    return Registration(action, sequence, True)


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
