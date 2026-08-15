#!/usr/bin/env python
"""Start the transcriber with your desktop session, and remove it again (Plan 5).

    uv run scripts/install_autostart.py
    uv run scripts/install_autostart.py --uninstall

Writes a **systemd user unit**, which is the right mechanism on this machine: it starts with the
session rather than the machine, restarts on failure, and is inspectable with `journalctl --user`.
An XDG autostart entry gives none of those.

It is written by a script rather than documented as a file to copy because the unit has to embed an
absolute path that differs per checkout — and because **uninstall is in scope from the start**. A
tool that installs a background service and cannot remove it is one users are right to distrust.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UNIT_NAME = "transcriber.service"
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"

UNIT_TEMPLATE = """\
[Unit]
Description=Live Seminar Transcriber
Documentation=file://{repo}/docs/documentation.md
# The graphical session, not the machine: window capture needs a compositor and a portal, and
# audio capture needs the user's own PipeWire.
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory={repo}
# `--no-takeover` is deliberate. The launcher's default is to stop an older instance and take the
# port, which is right at a terminal and wrong under a supervisor: systemd restarting a unit that
# then kills the instance systemd is already tracking is a loop.
ExecStart={uv} run app.py --no-takeover
Restart=on-failure
RestartSec=5
# Journald already timestamps every line.
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=graphical-session.target
"""


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed binary, fixed arguments
        ["systemctl", "--user", *args], capture_output=True, text=True, check=False
    )


def install() -> int:
    uv = shutil.which("uv")
    if uv is None:
        print(
            "uv is not on PATH, so the unit would not be able to start anything.", file=sys.stderr
        )
        return 2

    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    unit_path = UNIT_DIR / UNIT_NAME
    unit_path.write_text(UNIT_TEMPLATE.format(repo=REPO_ROOT, uv=uv), encoding="utf-8")
    print(f"  wrote {unit_path}")

    reload_result = systemctl("daemon-reload")
    if reload_result.returncode != 0:
        print(f"  systemctl daemon-reload failed: {reload_result.stderr.strip()}", file=sys.stderr)
        return 1

    enable_result = systemctl("enable", "--now", UNIT_NAME)
    if enable_result.returncode != 0:
        print(f"  could not enable it: {enable_result.stderr.strip()}", file=sys.stderr)
        return 1

    print(f"  enabled and started {UNIT_NAME}\n")
    print("  It now starts with your desktop session. To check on it:\n")
    print("      systemctl --user status transcriber")
    print("      journalctl --user -u transcriber -f\n")
    print("  To remove it:\n")
    print("      uv run scripts/install_autostart.py --uninstall\n")
    return 0


def uninstall() -> int:
    unit_path = UNIT_DIR / UNIT_NAME
    systemctl("disable", "--now", UNIT_NAME)
    removed = unit_path.exists()
    unit_path.unlink(missing_ok=True)
    systemctl("daemon-reload")

    print(f"  {'removed ' + str(unit_path) if removed else 'nothing was installed'}")
    print("  The transcriber no longer starts with your session.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uninstall", action="store_true", help="Remove the unit and stop starting on login."
    )
    args = parser.parse_args(argv)

    if shutil.which("systemctl") is None:
        print(
            "systemd is not available here. To start it another way, put a .desktop file in\n"
            "~/.config/autostart/ — see docs/deployment.md.",
            file=sys.stderr,
        )
        return 2

    print()
    return uninstall() if args.uninstall else install()


if __name__ == "__main__":
    raise SystemExit(main())
