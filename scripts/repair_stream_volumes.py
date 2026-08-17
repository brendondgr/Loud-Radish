#!/usr/bin/env python3
"""Undo the "everything is quiet but the mixer says 100%" fault.

## What goes wrong

PipeWire applies **two** gains to a playback stream and multiplies them together:

* ``channelVolumes`` — per channel. This is the one `pactl`, `wpctl`, plasma-pa and every other
  mixer displays and lets you drag.
* ``volume`` — a single scalar. **Nothing in the KDE interface shows it and nothing lets you change
  it.**

Measured on this machine with a stream deliberately set quiet::

    channelVolumes = 0.2847   -> the mixer shows this, as 66%
    volume         = 0.0200   -> invisible in the interface
    what you hear  = 0.00569   (-44.9 dB below full)

So a stream can read 100% everywhere you can look and still be inaudible. That is the whole of the
symptom "it shows full volume even though I can't hear it".

## Why it spreads to unrelated applications

WirePlumber remembers stream volumes in ``~/.local/state/wireplumber/stream-properties``, and when
an application has no entry under its own name it falls back to the entry for its **media role**.
Anything that plays music declares ``media.role=Music``, so one tool being set quiet under that role
makes *every* such application quiet: a video player, a music client's audio-only playback, and so
on. A browser is usually unaffected, because it has an entry under its own application name — which
is exactly why YouTube keeps working while everything else goes quiet.

``pw-play --volume=0.02`` is enough to cause it. The volume passed on the command line is saved to
the role, permanently, for every other application that shares it.

## Usage

    uv run python scripts/repair_stream_volumes.py            # report only
    uv run python scripts/repair_stream_volumes.py --fix      # repair and restart WirePlumber

Only *role* entries are repaired by default, because those are the ones that leak between
applications. Per-application entries are reported and left alone: a volume you set yourself on one
program is a setting, not a fault. ``--all`` repairs those too.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

STATE = Path.home() / ".local/state/wireplumber/stream-properties"

#: A saved line is ``<key>={<json>}``. The key is not JSON and may contain escaped spaces.
ENTRY = re.compile(r"^(?P<key>[^=]+)=(?P<blob>\{.*\})\s*$")

#: Anything below this is a real reduction rather than floating-point noise.
FULL = 0.99


def entries(text: str) -> list[tuple[str, dict, str]]:
    """Every saved stream property, as ``(key, parsed, original line)``."""
    found = []
    for line in text.splitlines():
        match = ENTRY.match(line)
        if not match:
            continue
        try:
            found.append((match["key"], json.loads(match["blob"]), line))
        except ValueError:
            continue
    return found


def reduced(props: dict) -> bool:
    """Whether this entry attenuates anything."""
    if props.get("volume", 1.0) < FULL:
        return True
    return any(level < FULL for level in props.get("channelVolumes", []))


def describe(key: str, props: dict) -> str:
    volume = props.get("volume", 1.0)
    channels = props.get("channelVolumes", [1.0])
    shown = round(max(channels) ** (1 / 3) * 100)
    audible = volume * max(channels)
    return (
        f"  {key}\n"
        f"      mixer shows ~{shown}%, hidden scalar {volume:.4f}, "
        f"actually {audible * 100:.2f}% of full"
    )


def restored(props: dict) -> dict:
    """The same entry at unity gain, keeping every field the caller did not ask about."""
    fixed = dict(props)
    fixed["volume"] = 1.0
    if "channelVolumes" in fixed:
        fixed["channelVolumes"] = [1.0] * len(fixed["channelVolumes"])
    fixed["mute"] = False
    return fixed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--fix", action="store_true", help="write the repair and restart WirePlumber"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="also reset per-application entries, not just media roles",
    )
    args = parser.parse_args()

    if not STATE.exists():
        print(f"No WirePlumber state at {STATE} — nothing to repair.")
        return 0

    text = STATE.read_text()
    found = entries(text)
    attenuated = [(key, props, line) for key, props, line in found if reduced(props)]

    if not attenuated:
        print("Every saved stream volume is at full. Nothing to repair.")
        return 0

    roles = [item for item in attenuated if ":media.role:" in item[0]]
    others = [item for item in attenuated if ":media.role:" not in item[0]]
    targets = attenuated if args.all else roles

    if roles:
        print("Media roles that are turned down — these leak between applications:")
        for key, props, _ in roles:
            print(describe(key, props))
    if others:
        print(
            "\nPer-application entries that are turned down"
            + (":" if args.all else " (left alone):")
        )
        for key, props, _ in others:
            print(describe(key, props))

    if not targets:
        print("\nNothing to repair by default. Re-run with --all to reset the entries above too.")
        return 0

    if not args.fix:
        print(f"\n{len(targets)} entr{'y' if len(targets) == 1 else 'ies'} would be reset to full.")
        print("Re-run with --fix to apply. WirePlumber restarts, which pauses audio for a moment.")
        return 0

    # **Edited with WirePlumber stopped, deliberately.** It holds this file open and rewrites it
    # from memory as streams come and go, so an edit made while it runs is overwritten the next
    # time anything plays.
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "stop", "wireplumber"], check=False)

    updated = text
    for key, props, line in targets:
        updated = updated.replace(
            line, f"{key}={json.dumps(restored(props), separators=(', ', ':'))}"
        )

    backup = STATE.with_suffix(".before-repair")
    backup.write_text(text)
    STATE.write_text(updated)

    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "start", "wireplumber"], check=False)

    print(f"\nReset {len(targets)} entr{'y' if len(targets) == 1 else 'ies'} to full volume.")
    print(f"The previous file is kept at {backup}")
    print("If anything was playing, restart it so it picks up the restored volume.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
