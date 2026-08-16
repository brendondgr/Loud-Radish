"""Finding the node that carries what this machine is playing.

Window mode needs the *window's* sound and not the user's voice, and the screen-sharing portal
carries video only — D-022 records that and it has not changed. So the audio is a separate capture,
and the thing to capture is the default sink's **monitor**: everything the machine plays, with no
microphone in it anywhere.

**PortAudio cannot reach it.** Enumerating this machine through `sounddevice` returns fifteen inputs
and not one of them is the `.monitor` of a sink; `pactl list short sources` shows them plainly. The
`loopback` kind the device list already reports is a name heuristic and is wrong here — it labels an
HDMI *output* as a loopback. Selecting a device is therefore not an option, and this module resolves
a PipeWire node name instead, which :class:`MonitorSource` hands to ``pw-record``.

**Resolved at start time, never stored.** The default sink changes when a dock is connected, when a
Bluetooth headset pairs, when a monitor with speakers wakes up. A stored node name would keep
pointing at a device that is no longer playing anything, and the recording would be silent with
nothing to say why — the same shape of fault as a recording that came out sixteen pixels tall.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)

#: How long to wait on the PipeWire tools. They answer in milliseconds when the daemon is healthy;
#: this is a guard against a hung daemon holding up the start of a recording, not a real budget.
TIMEOUT_S = 3.0

#: Every monitor node ends in this. PipeWire's own convention, and how a monitor is told from the
#: sink it belongs to.
MONITOR_SUFFIX = ".monitor"


class MonitorUnavailable(RuntimeError):
    """No monitor node could be found. The message names what is missing."""


def tools_present() -> bool:
    """Whether the PipeWire command-line tools this needs are installed."""
    return shutil.which("pw-record") is not None and shutil.which("pactl") is not None


def default_monitor() -> str:
    """The PipeWire node carrying what this machine is currently playing.

    Raises:
        MonitorUnavailable: when the tools are missing or no monitor node exists — which is a real
            answer on a machine with no sound card, not a failure to be papered over.
    """
    if shutil.which("pactl") is None:
        raise MonitorUnavailable(
            "pactl is not installed, so the system's audio output cannot be found. "
            "Install pipewire-utils (or your distribution's equivalent), or record the microphone."
        )

    sink = _run(["pactl", "get-default-sink"])
    if sink:
        candidate = sink if sink.endswith(MONITOR_SUFFIX) else sink + MONITOR_SUFFIX
        if candidate in _monitor_nodes():
            return candidate
        # The default sink exists but has no monitor. Unusual, and worth saying rather than
        # falling through silently to a different device's output.
        logger.info("The default sink %r has no monitor node; looking for another.", sink)

    available = _monitor_nodes()
    if not available:
        raise MonitorUnavailable(
            "This machine exposes no audio monitor, so what it plays cannot be recorded. "
            "Record the microphone instead."
        )

    chosen = available[0]
    logger.info("No default sink monitor; using %r.", chosen)
    return chosen


def _monitor_nodes() -> list[str]:
    """Every monitor source PipeWire is currently exposing, in the order it lists them."""
    listing = _run(["pactl", "list", "short", "sources"])
    names = []
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) > 1 and parts[1].endswith(MONITOR_SUFFIX):
            names.append(parts[1])
    return names


def _run(command: list[str]) -> str:
    """Run a PipeWire tool and return its stdout, or an empty string if it could not be run.

    Never raises. Every caller here has a meaningful answer for "could not tell", and a recording
    must not fail to start because a diagnostic command was slow.
    """
    try:
        result = subprocess.run(  # noqa: S603 - fixed binaries, no shell, no user input
            command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("%s failed: %s", command[0], exc)
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
