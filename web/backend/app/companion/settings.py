"""``python -m app.companion.settings`` — the native settings window (D-051).

A module of its own so the tray menu has a short, stable command to launch, and so the window runs
in a process separate from the companion's D-Bus loop.
"""

from __future__ import annotations

from .settings_window import main

if __name__ == "__main__":
    raise SystemExit(main())
