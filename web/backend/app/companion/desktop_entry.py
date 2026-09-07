"""One `.desktop` file per shortcut, because that is what the desktop can actually bind to.

**Measured on a real Plasma 6.7 Wayland session, after both alternatives were tried and failed.**

- The **XDG GlobalShortcuts portal** (`org.freedesktop.portal.GlobalShortcuts`) is present and is
  the obvious modern answer. It refuses this application outright: `CreateSession` returns
  *"An app id is required"*, because the portal derives an app id from a sandbox and there is no
  sandbox here. Nothing can be passed over D-Bus to supply one.
- Registering as an ordinary **KGlobalAccel component** does work, and delivers
  `globalShortcutPressed` in-process. It is not used, for one reason: it only works while the
  companion is running. A shortcut that stops existing when a helper process dies — and says
  nothing when it does — is a shortcut nobody can rely on mid-talk.

What is used instead is what KDE's own *Add Command* shortcuts are: a `.desktop` entry, registered
as a **service** component whose single action is `_launch`. KDE stores it in
`~/.config/kglobalshortcutsrc`, installs the grab immediately, honours it whether or not this
application is running, restores it after a reboot, and shows it in System Settings where the user
can change or revoke it. Verified end to end: bound `Ctrl+Alt+Shift+F9`, pressed it with a
synthetic key, and watched the command run.

The cost is a process per press. Measured at **70 ms** for the control script, which is well under
the threshold where a keypress stops feeling immediate.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import branding

logger = logging.getLogger(__name__)


def applications_dir() -> Path:
    """Where per-user `.desktop` files live, honouring `XDG_DATA_HOME`."""
    root = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(root) / "applications"


def entry_name(action: str) -> str:
    """The file name for one action's entry.

    One file per action rather than one file with several actions: a service component's shortcut
    is always the action `_launch`, so two shortcuts in one entry would be two names for one key.
    """
    return f"{branding.APP_SLUG}-{action.replace('_', '-')}.desktop"


def entry_path(action: str) -> Path:
    return applications_dir() / entry_name(action)


def command_for(repo_root: Path | str, arguments: list[str]) -> str:
    """The `Exec=` line: an absolute interpreter and an absolute script.

    `sys.executable` is resolved at install time, which is the virtual environment's Python when
    the companion installs these. A bare ``uv run`` would need `uv` on the PATH of whatever process
    the desktop uses to launch it, and that PATH is not this one.
    """
    script = Path(repo_root) / branding.CONTROL_SCRIPT
    return " ".join([sys.executable, str(script), *arguments])


def install(action: str, label: str, arguments: list[str], repo_root: Path | str) -> Path:
    """Write one entry and return where it went. Overwrites, because the command may have moved."""
    path = entry_path(action)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={branding.APP_NAME}: {label}\n"
        f"Comment={label}\n"
        f"Exec={command_for(repo_root, arguments)}\n"
        # Not in the application menu: these are shortcut targets, and a menu full of "Loud Radish:
        # Stop recording" entries nobody would ever click is clutter the user did not ask for.
        "NoDisplay=true\n"
        "Terminal=false\n"
        f"Icon={branding.APP_SLUG}\n",
        encoding="utf-8",
    )
    return path


def remove(action: str) -> bool:
    """Delete one entry. Returns whether there was one."""
    path = entry_path(action)
    if not path.exists():
        return False
    path.unlink()
    return True


def refresh_database() -> None:
    """Ask the desktop to re-read the directory. Absent tooling is not an error."""
    tool = shutil.which("update-desktop-database")
    if not tool:
        return
    try:
        subprocess.run([tool, str(applications_dir())], check=False, timeout=10)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        logger.debug("Could not refresh the desktop database: %s", exc)
