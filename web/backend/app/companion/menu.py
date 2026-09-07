"""The tray menu: what it offers, and when each item is available (Plan 5).

The brief asks for both "a toggle for enabling and disabling the app" and per-mode recording
control. **Those are different things and the menu carries both.** Conflating them would mean
disabling the app killed a recording in progress, which is the opposite of what someone reaching
for a disable switch mid-talk would want.

Built as data rather than as D-Bus calls so the structure is testable without a session bus, which
is the part most likely to be wrong: an item enabled when it should not be is a menu that starts a
second recording over the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import branding
from ..services.session import modes
from .animation import Snapshot


@dataclass
class MenuItem:
    """One row."""

    id: str
    label: str
    enabled: bool = True
    #: `standard`, `separator`, or `checkmark`.
    kind: str = "standard"
    checked: bool = False
    children: list[MenuItem] = field(default_factory=list)

    def as_dbus(self) -> dict[str, Any]:
        """The property map `com.canonical.dbusmenu` expects."""
        properties: dict[str, Any] = {"label": self.label, "enabled": self.enabled}
        if self.kind == "separator":
            properties = {"type": "separator"}
        elif self.kind == "checkmark":
            properties["toggle-type"] = "checkmark"
            properties["toggle-state"] = 1 if self.checked else 0
        return properties


def build(snapshot: Snapshot, *, listening: bool) -> list[MenuItem]:
    """The whole menu for one moment.

    Args:
        listening: whether global shortcuts are armed. Deliberately independent of whether a
            recording is running — see the module docstring.
    """
    if not snapshot.reachable:
        # Nothing else is worth offering: every other item would fail at the moment it was clicked,
        # and a menu full of items that do not work is worse than a short one that says why.
        return [
            MenuItem("status", f"{branding.APP_NAME} is not running", enabled=False),
            MenuItem("sep", "", kind="separator"),
            MenuItem("open", "Open the interface"),
            MenuItem("quit", "Quit this tray icon"),
        ]

    recording = snapshot.state in (modes.RECORDING, modes.STOPPING, modes.ARMING)
    busy = snapshot.state in (modes.STOPPING, modes.PROCESSING)

    items = [MenuItem("status", _status_line(snapshot), enabled=False)]
    items.append(MenuItem("sep-1", "", kind="separator"))

    if recording:
        items.append(MenuItem("stop", "Stop recording", enabled=not busy))
    else:
        items.append(MenuItem("start-live", "Start live transcription", enabled=not busy))
        items.append(MenuItem("start-recorded", "Start recording only", enabled=not busy))
        # Window capture needs its three options answered before anything is captured, so it opens
        # the browser's own sheet rather than starting. Building a second native dialog would mean
        # two implementations of the same three toggles to keep in step.
        items.append(MenuItem("arm-window", "Record a window…", enabled=not busy))

    items.append(MenuItem("sep-2", "", kind="separator"))
    items.append(
        MenuItem("listening", "Listening for shortcuts", kind="checkmark", checked=listening)
    )
    items.append(MenuItem("sep-3", "", kind="separator"))
    items.append(MenuItem("open", "Open the interface"))
    items.append(MenuItem("settings", "Settings…"))
    items.append(MenuItem("quit", "Quit this tray icon"))
    return items


def _status_line(snapshot: Snapshot) -> str:
    """What it is doing, in words. The icon says it too, but not everyone reads icons."""
    if snapshot.state == modes.PROCESSING:
        return "Transcribing the recording…"
    if snapshot.state == modes.STOPPING:
        return "Stopping…"
    if snapshot.state == modes.ARMING:
        return "Choosing a window…"
    if snapshot.state == modes.ERROR:
        return "Something went wrong"
    if snapshot.state == modes.RECORDING:
        return f"Recording · {snapshot.mode}"
    if snapshot.running_pass:
        return "Tidying the transcript…"
    return "Ready"
