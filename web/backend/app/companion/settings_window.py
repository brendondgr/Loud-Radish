"""A settings window that is not the web application (D-051).

Asked for directly: a place reached by right-clicking the tray icon where the keyboard shortcuts
can be edited, and the microphone chosen, without opening a browser tab.

**Tk, because it costs nothing.** `tkinter` is already in the virtual environment — it ships with
CPython — and it was checked on this machine before this was written: the window opens under
XWayland and a key chord arrives with its keysym and modifier mask intact. The alternative was
PySide6, a hundred-odd megabytes for one dialog, in a repository that hand-wrote a rasteriser to
avoid depending on Pillow.

**Its own process.** Tk's main loop must own the thread it runs on, and the companion's D-Bus
dispatch loop already owns one. Running separately also means a crash in here cannot take the tray
icon down with it, which matters because the tray icon is the only way back to this window.

**It owns no settings.** Everything is read through `GET /api/config` and written through
`PATCH /api/config`; the backend owns configuration and the frontend reads it, presents it, and
writes it back. A native window is not the exception to that rule just because it is not a browser —
if it kept its own idea of the settings there would be two, and they would drift.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from .. import branding
from . import keys, settings_form
from .settings_form import NotAShortcut

logger = logging.getLogger(__name__)

TIMEOUT_S = 5.0

#: Which shortcuts to offer, in the order they are worth seeing. Dictation first: it is the one
#: people press dozens of times a day.
SHORTCUT_ORDER = ("dictate", "toggle_live", "toggle_recorded", "arm_window", "stop", "open_app")

PRESS_A_KEY = "press a key…"


def _shown(sequence: str | None) -> str:
    """A stored sequence as a label. `Meta` is the Windows key and nobody calls it Meta."""
    return keys.display_sequence(sequence) if sequence else "unset"


class Api:
    """The server, over the same loopback HTTP the browser uses."""

    def __init__(self, base: str) -> None:
        self.base = base

    def config(self) -> dict[str, Any]:
        return self._get("/api/config").get("config") or {}

    def devices(self) -> list[dict[str, Any]]:
        return self._get("/api/audio/devices").get("devices") or []

    def patch(self, edits: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(settings_form.patch_payload(edits)).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - loopback
            f"{self.base}/api/config",
            data=body,
            headers={"Content-Type": "application/json"},
            method="PATCH",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(f"{self.base}{path}")  # noqa: S310 - loopback
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))


def desktop_holder(sequence: str) -> str:
    """Who on this desktop already owns ``sequence``. Empty when nobody does, or when unknowable."""
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection

        from . import desktop_entry, keys, shortcuts

        chord = keys.parse_sequence(sequence)
        address = DBusAddress(
            object_path=shortcuts.KGLOBALACCEL_PATH,
            bus_name=shortcuts.KGLOBALACCEL_BUS,
            interface=shortcuts.KGLOBALACCEL_IFACE,
        )
        ours = {desktop_entry.entry_name(name) for name in shortcuts.ACTIONS}
        with open_dbus_connection(bus="SESSION") as connection:
            reply = connection.send_and_get_reply(
                new_method_call(
                    address, "globalShortcutsByKey", "(ai)(i)", (([chord, 0, 0, 0],), (0,))
                )
            )
        for entry in reply.body[0]:
            if entry[2] in ours:
                continue
            return f"{entry[3] or entry[2]} ({entry[1] or entry[0]})"
    except Exception as exc:  # noqa: BLE001 - a desktop that cannot answer is not a conflict
        logger.debug("Could not ask the desktop who holds %s: %s", sequence, exc)
    return ""


class SettingsWindow:
    """The window. Built once, in one method per group, and closed by the user."""

    def __init__(self, api: Api, *, holder=desktop_holder) -> None:  # noqa: ANN001
        self.api = api
        self.holder = holder
        self.config: dict[str, Any] = {}
        self.devices: list[dict[str, Any]] = []
        self.sequences: dict[str, str] = {}
        self.capturing: str | None = None
        self._widgets: dict[str, Any] = {}
        self.root = None
        self.status = None

    # -- building ----------------------------------------------------------------------

    def open(self) -> int:
        import tkinter as tk
        from tkinter import ttk

        try:
            self.config = self.api.config()
            self.devices = self.api.devices()
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            logger.error("%s is not running: %s", branding.APP_NAME, exc)
            return 1

        self.sequences = dict(self.config.get("shortcuts") or {})

        self.root = tk.Tk()
        self.root.title(f"{branding.APP_NAME} — Settings")
        self.root.minsize(560, 480)
        # Tk's own theme on Linux is from 1995. `clam` is the least unlike a modern desktop of the
        # themes that ship with it, and needs no files of its own.
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        notebook.add(self._shortcuts_tab(notebook), text="Shortcuts")
        notebook.add(self._microphone_tab(notebook), text="Microphone")
        notebook.add(self._dictation_tab(notebook), text="Dictation")

        self.status = ttk.Label(self.root, text="", anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 4))
        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(footer, text="Close", command=self.root.destroy).pack(side="right")
        ttk.Button(footer, text="Re-register shortcuts", command=self._rebind).pack(
            side="right", padx=6
        )

        self.root.mainloop()
        return 0

    def _shortcuts_tab(self, parent):  # noqa: ANN001, ANN202
        from tkinter import ttk

        from .shortcuts import ACTIONS

        frame = ttk.Frame(parent, padding=12)
        ttk.Label(
            frame,
            text=(
                "Click a shortcut, then press the keys you want — for example hold Win and Shift "
                "and press Space. A key already used by something else is refused rather than "
                "taken.\n\nWin is the Windows key. KDE calls it Meta, which is what you will see "
                "if you look these up in System Settings."
            ),
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        for row, action in enumerate(SHORTCUT_ORDER, start=1):
            if action not in ACTIONS:
                continue
            ttk.Label(frame, text=ACTIONS[action][0]).grid(row=row, column=0, sticky="w", pady=3)
            button = ttk.Button(
                frame,
                text=_shown(self.sequences.get(action)),
                width=22,
                command=lambda a=action: self._begin_capture(a),
            )
            button.grid(row=row, column=1, sticky="e", pady=3)
            self._widgets[action] = button

        frame.columnconfigure(0, weight=1)
        return frame

    def _microphone_tab(self, parent):  # noqa: ANN001, ANN202
        import tkinter as tk
        from tkinter import ttk

        frame = ttk.Frame(parent, padding=12)
        ttk.Label(
            frame, text="Which input to listen to. Saved as soon as you choose it.", wraplength=520
        ).pack(anchor="w", pady=(0, 10))

        chosen = (self.config.get("audio") or {}).get("device_id")
        self._device_var = tk.StringVar(value=chosen or "")
        listing = ttk.Frame(frame)
        listing.pack(fill="both", expand=True)

        for device in self.devices:
            if device.get("kind") == "file":
                continue
            ttk.Radiobutton(
                listing,
                text=device.get("name") or device.get("id"),
                value=device.get("id"),
                variable=self._device_var,
                command=self._choose_device,
            ).pack(anchor="w", pady=1)
        return frame

    def _dictation_tab(self, parent):  # noqa: ANN001, ANN202
        import tkinter as tk
        from tkinter import ttk

        settings = self.config.get("dictation") or {}
        frame = ttk.Frame(parent, padding=12)
        ttk.Label(
            frame,
            text=(
                "Press the dictation shortcut, speak, press it again. The words are typed into "
                "whatever window has focus."
            ),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        self._tidy_var = tk.BooleanVar(value=settings.get("cleanup") == "llm")
        ttk.Checkbutton(
            frame,
            text="Tidy the punctuation with the language model (about a second)",
            variable=self._tidy_var,
            command=lambda: self._save(
                {"dictation.cleanup": "llm" if self._tidy_var.get() else "off"}
            ),
        ).pack(anchor="w", pady=3)

        self._paste_var = tk.BooleanVar(value=bool(settings.get("paste", True)))
        ttk.Checkbutton(
            frame,
            text="Paste it for me (otherwise it is only copied)",
            variable=self._paste_var,
            command=lambda: self._save({"dictation.paste": self._paste_var.get()}),
        ).pack(anchor="w", pady=3)

        chord = ttk.Frame(frame)
        chord.pack(anchor="w", pady=(10, 3), fill="x")
        ttk.Label(chord, text="Paste with:").pack(side="left")
        self._chord_var = tk.StringVar(value=settings.get("paste_chord", "ctrl+v"))
        picker = ttk.Combobox(
            chord,
            textvariable=self._chord_var,
            values=["ctrl+v", "ctrl+shift+v"],
            width=16,
            state="readonly",
        )
        picker.pack(side="left", padx=8)
        picker.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._save({"dictation.paste_chord": self._chord_var.get()}),
        )
        ttk.Label(
            frame,
            text=(
                "Terminals paste with Ctrl+Shift+V — Ctrl+V there means "
                "“quote the next character”. Nothing can ask the desktop what kind of "
                "window has focus, so this is a choice rather than something detected."
            ),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
        return frame

    # -- behaviour ---------------------------------------------------------------------

    def _begin_capture(self, action: str) -> None:
        self.capturing = action
        self._widgets[action].configure(text=PRESS_A_KEY)
        self._say("Press the keys you want, or Escape to leave it alone.")
        self.root.bind("<KeyPress>", self._on_key)

    def _on_key(self, event) -> None:  # noqa: ANN001
        action, self.capturing = self.capturing, None
        if action is None:
            return
        self.root.unbind("<KeyPress>")

        if (event.keysym or "").lower() == "escape":
            self._widgets[action].configure(text=_shown(self.sequences.get(action)))
            self._say("")
            return

        try:
            sequence = settings_form.chord_from_event(event.keysym, int(event.state))
        except NotAShortcut as exc:
            self._widgets[action].configure(text=_shown(self.sequences.get(action)))
            self._say(str(exc))
            return

        clash = settings_form.conflict_for(sequence, action, self.sequences, self.holder)
        if clash:
            self._widgets[action].configure(text=_shown(self.sequences.get(action)))
            self._say(f"{_shown(sequence)} is already used by {clash}.")
            return

        self.sequences[action] = sequence
        self._widgets[action].configure(text=_shown(sequence))
        self._save({f"shortcuts.{action}": sequence}, note=f"{_shown(sequence)} saved.")

    def _choose_device(self) -> None:
        self._save({"audio.device_id": self._device_var.get()}, note="Microphone saved.")

    def _save(self, edits: dict[str, Any], note: str = "Saved.") -> None:
        try:
            self.api.patch(edits)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            self._say(f"Could not save: {exc}")
            return
        self._say(note)

    def _rebind(self) -> None:
        """Ask the desktop to take the current keys. Needed after editing one."""
        from .shortcuts import describe, register_all

        class _Values:
            def __init__(self, values: dict[str, str]) -> None:
                self.__dict__.update(values)

            def __getattr__(self, _name: str) -> str:
                return ""

        import os

        root = os.environ.get(branding.env_var("ROOT")) or "."
        results = register_all(_Values({"enabled": True, **self.sequences}), root)
        logger.info("Shortcuts:\n%s", describe(results, root))
        failed = [entry for entry in results if not entry.registered and entry.sequence]
        if failed:
            self._say(f"{len(failed)} could not be registered — see the log for which.")
        else:
            self._say("All shortcuts registered with the desktop.")

    def _say(self, message: str) -> None:
        if self.status is not None:
            self.status.configure(text=message)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(prog=f"{branding.APP_SLUG}-settings", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("API_PORT", 8395)))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return SettingsWindow(Api(f"http://{args.host}:{args.port}")).open()


if __name__ == "__main__":
    raise SystemExit(main())
