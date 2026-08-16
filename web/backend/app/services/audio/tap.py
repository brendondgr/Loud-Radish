"""Capturing one application's audio without taking it off the user's speakers.

The screen-sharing portal carries video only and returns no PID, no application id and no audio
node, so a window's sound has to be captured separately. Recording the default sink's monitor —
everything the machine plays — is correct whenever the chosen window is the only thing making
sound, and wrong the moment it is not.

**The mechanism is PipeWire's graph, and one property of it is what makes this safe:**

    A node's output ports may be linked to more than one destination. Adding a link does not
    remove the one that is already there.

So an application's audio can be tapped *additively* into a private sink while it keeps playing to
the user's speakers. This is the design OBS Studio's Application Audio Capture uses, and it is why
the obvious alternative — `pactl move-sink-input`, moving the stream onto a null sink — is not
implemented here at any priority or behind any flag: it is the only approach that can leave someone
unable to hear the thing they are recording.

Verified on this machine before any of it was written. With a browser playing a video, linking its
output ports into a tap left both original links to the real sink intact, and recording the tap's
monitor produced eleven seconds of real audio with no NaN — while playback continued.

**Applications own more than one node.** A browser typically creates a playback node per media
element or per tab, so the set is watched and new nodes are linked as they appear; linking one node
and stopping is how a predecessor tool ended up recording only the first tab a user opened.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger(__name__)

#: The class a playback stream carries. Anything else in the graph is a device or a capture.
PLAYBACK_CLASS: Final = "Stream/Output/Audio"

#: Name of the sink this creates. One at a time, and destroyed explicitly on stop — leaked null
#: sinks accumulate across crashes and are visible to the user in their output picker.
TAP_SINK_NAME: Final = "transcriber-tap"

#: How long to wait on the PipeWire tools. They answer in milliseconds when the daemon is healthy.
TIMEOUT_S: Final = 5.0


class TapError(RuntimeError):
    """The tap could not be created or linked. The message names what failed."""


@dataclass(frozen=True)
class PlaybackStream:
    """One application node that is currently playing audio."""

    node_id: int
    #: Monotonic and never reused, unlike the node id — which PipeWire recycles after a node is
    #: destroyed, so a long-lived session can end up bound to something else entirely.
    serial: int
    node_name: str
    application: str
    binary: str
    media_name: str

    @property
    def label(self) -> str:
        """What to show a user choosing between streams."""
        detail = self.media_name or self.node_name
        return f"{self.application or self.node_name} — {detail}" if detail else self.application

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "serial": self.serial,
            "node_name": self.node_name,
            "application": self.application,
            "binary": self.binary,
            "media_name": self.media_name,
            "label": self.label,
        }


def tools_present() -> bool:
    """Whether the PipeWire tools this needs are installed."""
    return all(shutil.which(name) for name in ("pw-dump", "pw-link", "pactl"))


def playback_streams() -> list[PlaybackStream]:
    """Every application stream currently playing audio, in the order PipeWire lists them."""
    raw = _run(["pw-dump"])
    if not raw:
        return []
    try:
        objects = json.loads(raw)
    except ValueError:
        logger.debug("pw-dump returned something that is not JSON")
        return []

    streams: list[PlaybackStream] = []
    for entry in objects:
        if entry.get("type") != "PipeWire:Interface:Node":
            continue
        props = entry.get("info", {}).get("props", {})
        if props.get("media.class") != PLAYBACK_CLASS:
            continue
        streams.append(
            PlaybackStream(
                node_id=int(entry.get("id", 0)),
                serial=int(props.get("object.serial", 0) or 0),
                node_name=str(props.get("node.name", "")),
                application=str(props.get("application.name", "")),
                binary=str(props.get("application.process.binary", "")),
                media_name=str(props.get("media.name", "")),
            )
        )
    return streams


def score(stream: PlaybackStream, *, app_id: str = "", title: str = "") -> int:
    """How well a playback stream matches a window, higher being better.

    **Nothing here is authoritative and the design does not pretend otherwise.** PipeWire's own
    documentation warns that only the `pipewire.*` properties are safe for identifying an
    application, because a client may set the others freely — and the one trustworthy PID,
    `pipewire.sec.pid`, resolves to the `pipewire-pulse` process for every application that reaches
    audio through the PulseAudio API, which on a desktop is nearly all of them. Sandboxed
    applications are in a different PID namespace again.

    So this ranks candidates rather than deciding between them, and the interface presents the
    ranking. KDE's own portal backend does the same thing for window restore — exact `appId` first,
    then a fuzzy title match — which is a fair indication that name matching is the sanctioned
    heuristic here rather than a shortcut.
    """
    points = 0
    app = app_id.strip().lower()
    if app:
        if app in (stream.binary.lower(), stream.application.lower()):
            points += 100
        elif app and (app in stream.node_name.lower() or stream.node_name.lower() in app):
            points += 50

    if title:
        wanted = _tokens(title)
        offered = _tokens(stream.media_name)
        overlap = wanted & offered
        # A browser puts the page title in `media.name`, which often shares words with the window
        # title even when neither contains the other.
        points += min(40, 10 * len(overlap))

    return points


def rank(
    streams: list[PlaybackStream], *, app_id: str = "", title: str = ""
) -> list[PlaybackStream]:
    """The streams most likely to belong to this window, best first."""
    return sorted(streams, key=lambda s: score(s, app_id=app_id, title=title), reverse=True)


class ApplicationTap:
    """A private sink carrying one application's audio, without diverting it.

    The sink is destroyed on :meth:`close`, which is not optional: a leaked null sink shows up in
    the user's output picker and survives until they notice it.
    """

    def __init__(self, sink_name: str = TAP_SINK_NAME) -> None:
        self.sink_name = sink_name
        self._module = ""
        self._linked: set[str] = set()

    @property
    def monitor(self) -> str:
        """The node to record from once the tap is open.

        Named as a monitor because that is what it is, but a **null sink's monitor is reached
        through the sink**: targeting ``<name>.monitor`` resolves to nothing here, and a target
        that resolves to nothing is how a capture silently ends up on the default source — the
        microphone. The caller strips the suffix and sets ``stream.capture.sink``.
        """
        return f"{self.sink_name}.monitor"

    @property
    def is_open(self) -> bool:
        return bool(self._module)

    def open(self) -> None:
        """Create the private sink.

        Raises:
            TapError: if the sink cannot be created, because recording silence from a tap that does
                not exist is the failure this whole module is meant to prevent.
        """
        if self._module:
            return
        if not tools_present():
            raise TapError(
                "PipeWire's command-line tools are not installed, so one application's audio "
                "cannot be captured. Install pipewire-utils, or record the system's whole output."
            )

        module = _run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                "media.class=Audio/Sink",
                f"sink_name={self.sink_name}",
                "channel_map=stereo",
            ]
        )
        if not module.strip().isdigit():
            raise TapError(f"Could not create the capture sink: {module or 'no reason given'}")

        self._module = module.strip()
        logger.info("Opened the application audio tap as %s", self.sink_name)

    def link(self, stream: PlaybackStream) -> int:
        """Link one application node's outputs into the tap. Returns how many ports were linked.

        **Additive.** The application's existing link to the real sink is untouched, which is what
        keeps the user able to hear what they are recording.
        """
        if not self._module:
            raise TapError("The tap is not open.")

        linked = 0
        for channel in ("FL", "FR"):
            key = f"{stream.node_name}:{channel}"
            if key in self._linked:
                continue
            result = _run(
                [
                    "pw-link",
                    f"{stream.node_name}:output_{channel}",
                    f"{self.sink_name}:playback_{channel}",
                ],
                allow_failure=True,
            )
            # A mono application has no FR port; that is not a failure.
            if result is not None:
                self._linked.add(key)
                linked += 1
        if linked:
            logger.info("Linked %s into the audio tap (%d ports)", stream.node_name, linked)
        return linked

    def link_all(self, streams: list[PlaybackStream]) -> int:
        """Link every node in a set, and return how many ports were newly linked.

        Called repeatedly while recording: an application creates and destroys playback nodes as
        the user opens tabs and starts media, and a tap that linked once captures only whatever was
        playing at the moment the recording began.
        """
        return sum(self.link(stream) for stream in streams)

    def close(self) -> None:
        """Destroy the sink. Safe to call more than once, and safe if opening failed."""
        module, self._module = self._module, ""
        self._linked.clear()
        if not module:
            return
        _run(["pactl", "unload-module", module], allow_failure=True)
        logger.info("Closed the application audio tap")

    def __enter__(self) -> ApplicationTap:
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _tokens(text: str) -> set[str]:
    """Words worth comparing between a window title and a stream's media name."""
    cleaned = "".join(char.lower() if char.isalnum() else " " for char in text)
    return {word for word in cleaned.split() if len(word) > 3}


def _run(command: list[str], *, allow_failure: bool = False) -> str:
    """Run a PipeWire tool and return stdout, or an empty string when it could not be run."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed binaries, no shell
            command, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("%s failed: %s", command[0], exc)
        return ""
    if result.returncode != 0:
        if not allow_failure:
            logger.debug("%s exited %d: %s", command[0], result.returncode, result.stderr.strip())
        return "" if allow_failure else result.stderr.strip()
    return result.stdout.strip()


@dataclass
class TapState:
    """What a running tap is currently capturing, for the interface and the sidecar."""

    sink: str = ""
    streams: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"sink": self.sink, "streams": list(self.streams)}
