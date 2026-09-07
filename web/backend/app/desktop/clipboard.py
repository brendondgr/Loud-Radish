"""Putting text on the clipboard, by whichever route this desktop actually has.

Three, tried in order, each checked before it is used:

1. **`wl-copy`** — the Wayland clipboard. What this machine uses.
2. **`org.kde.klipper`** over D-Bus — KDE's clipboard manager, which is on the session bus here.
   Worth having as more than a fallback: `wl-copy` forks a process that *serves* the clipboard for
   as long as anything might paste from it, whereas Klipper takes ownership itself and keeps the
   text in its history, so a dictation you miss is still recoverable from the clipboard menu.
3. **`xclip`** — X11, for a session that is not Wayland.

Neither of the first two is theoretical: both were round-tripped on this machine before this module
was written, and the developer's own clipboard was saved and restored around the test.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

from .outcome import Outcome

logger = logging.getLogger(__name__)

#: Generous: `wl-copy` forks a server and returns, and Klipper is a local call. Anything slower
#: than this is a desktop in trouble, and waiting longer will not fix it.
TIMEOUT_S = 5.0

KLIPPER_BUS = "org.kde.klipper"
KLIPPER_PATH = "/klipper"
KLIPPER_IFACE = "org.kde.klipper.klipper"


def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _x11() -> bool:
    return bool(os.environ.get("DISPLAY"))


def _via_wl_copy(text: str) -> Outcome | None:
    tool = shutil.which("wl-copy")
    if not tool or not _wayland():
        return None
    try:
        subprocess.run(  # noqa: S603
            [tool], input=text.encode("utf-8"), check=True, timeout=TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Outcome(False, "wl-copy", str(exc))
    return Outcome(True, "wl-copy")


def _via_klipper(text: str) -> Outcome | None:
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return None

    try:
        with open_dbus_connection(bus="SESSION") as connection:
            address = DBusAddress(
                object_path=KLIPPER_PATH, bus_name=KLIPPER_BUS, interface=KLIPPER_IFACE
            )
            connection.send_and_get_reply(
                new_method_call(address, "setClipboardContents", "s", (text,))
            )
    except Exception as exc:  # noqa: BLE001 - not running, or not KDE
        return Outcome(False, "klipper", str(exc))
    return Outcome(True, "klipper")


def _via_xclip(text: str) -> Outcome | None:
    tool = shutil.which("xclip")
    if not tool or not _x11():
        return None
    try:
        subprocess.run(  # noqa: S603
            [tool, "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Outcome(False, "xclip", str(exc))
    return Outcome(True, "xclip")


#: In order. `wl-copy` first because it is the plainest and needs no clipboard manager running.
#:
#: **Names, not functions.** A tuple of function objects binds them at import, so a test that
#: replaces one is ignored and quietly falls through to the *real* backend — which on a KDE desktop
#: means the suite writing to the developer's actual clipboard. Resolved by name at call time
#: instead; this is the same lesson as the four in `tests/conftest.py`, arrived at from the other
#: direction.
BACKENDS = ("wl-copy", "klipper", "xclip")


def _backend(name: str):  # noqa: ANN202
    return {"wl-copy": _via_wl_copy, "klipper": _via_klipper, "xclip": _via_xclip}[name]


def copy(text: str) -> Outcome:
    """Put ``text`` on the clipboard. Never raises.

    An empty string is refused rather than written: clearing the clipboard is not what any caller
    here wants, and doing it silently would destroy whatever the user had copied.
    """
    if not text:
        return Outcome(False, detail="there was nothing to copy")

    attempted: list[str] = []
    for name in BACKENDS:
        result = _backend(name)(text)
        if result is None:
            continue  # not available on this desktop; not a failure worth reporting
        attempted.append(name)
        if result.ok:
            return Outcome(True, result.backend, attempts=attempted)
        logger.debug("Clipboard backend %s failed: %s", name, result.detail)

    return Outcome(False, detail="no clipboard tool worked", attempts=attempted)


def read_back() -> str:
    """What is on the clipboard now, or an empty string. For verifying a copy actually landed."""
    tool = shutil.which("wl-paste") if _wayland() else shutil.which("xclip")
    if not tool:
        return ""
    arguments = (
        [tool, "--no-newline"]
        if tool.endswith("wl-paste")
        else [tool, "-o", "-selection", "clipboard"]
    )
    try:
        done = subprocess.run(  # noqa: S603
            arguments, capture_output=True, timeout=TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.decode("utf-8", errors="replace")
