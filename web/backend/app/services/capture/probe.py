"""Whether this machine can capture a window, and if not, exactly which part is missing (D-022).

Window capture has **five independent ways to be unavailable**, and each has a different fix:

1. The Python D-Bus client is not installed — ``uv sync`` puts it back.
2. The desktop has no screen-cast portal at all — a compositor problem, not ours.
3. The portal exists but does not offer *window* sources — some only share whole monitors.
4. GStreamer is not installed — a system package.
5. GStreamer is installed but lacks an encoder or the PipeWire source.

One "window capture unavailable" for all five is the failure mode that generates support requests,
so the verdict names the specific piece and the specific remedy. The interface disables the mode
with that reason rather than hiding it (D-020).

Every check is defensive: a probe that raises would take down the health endpoint, which is the one
thing that must answer when everything else is unhappy.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final

logger = logging.getLogger(__name__)

#: Portal source-type bits from the ScreenCast interface. We need WINDOW.
SOURCE_MONITOR: Final = 1
SOURCE_WINDOW: Final = 2
SOURCE_VIRTUAL: Final = 4

#: GStreamer elements the pipeline cannot be built without, whatever else is chosen.
REQUIRED_ELEMENTS: Final[tuple[str, ...]] = ("pipewiresrc", "videoconvert", "videorate")

#: Video encoders, best first. **`x264enc` is deliberately not first**: it is absent from stock
#: Fedora and most distributions that cannot ship x264, so preferring it would make the common case
#: report a missing encoder. VP8 is present wherever `gst-plugins-good` is.
ENCODERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("vp8enc", "webmmux", "webm"),
    ("vp9enc", "webmmux", "webm"),
    ("x264enc", "mp4mux", "mp4"),
    ("openh264enc", "matroskamux", "mkv"),
)

#: The preview branch. Optional: without it the monitor shows a static card instead of frames,
#: which is a documented degraded state rather than a failure (D-020).
PREVIEW_ELEMENT: Final = "jpegenc"

#: How long to wait on `gst-inspect-1.0`. It is a local binary listing a local plugin; anything
#: slower than this means something is wrong and the health endpoint should not hang on it.
INSPECT_TIMEOUT_S: Final = 5.0


@dataclass(frozen=True)
class CaptureSupport:
    """What this machine can do, and what is stopping it if it cannot."""

    available: bool
    #: The single missing piece, as a key the interface can branch on.
    missing: str = ""
    #: What to do about it, in a sentence a user can act on.
    reason: str = ""
    session_type: str = ""
    portal_version: int = 0
    #: The chosen `(encoder, muxer, extension)`, when one is available.
    encoder: str = ""
    muxer: str = ""
    extension: str = ""
    #: Whether a live preview is possible. False degrades the monitor, not the recording.
    preview: bool = False
    #: Every element that was looked for and not found, for the diagnostics panel.
    missing_elements: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "missing": self.missing,
            "reason": self.reason,
            "session_type": self.session_type,
            "portal_version": self.portal_version,
            "encoder": self.encoder,
            "muxer": self.muxer,
            "extension": self.extension,
            "preview": self.preview,
            "missing_elements": list(self.missing_elements),
        }


def session_type() -> str:
    """``wayland``, ``x11``, or ``""`` when there is no graphical session at all."""
    declared = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if declared in ("wayland", "x11"):
        return declared
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return ""


def dbus_client_available() -> bool:
    """Whether the pure-Python D-Bus client is installed."""
    import importlib.util

    return importlib.util.find_spec("jeepney") is not None


@lru_cache(maxsize=1)
def gstreamer_elements() -> frozenset[str]:
    """Every GStreamer element this machine has, by name.

    Cached: `gst-inspect-1.0` walks the whole plugin registry, and the answer cannot change without
    installing a package — which is not something to re-check on every health request.
    """
    binary = shutil.which("gst-inspect-1.0")
    if binary is None:
        return frozenset()

    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, no shell, no user input
            [binary],
            capture_output=True,
            text=True,
            timeout=INSPECT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Could not list GStreamer elements", exc_info=True)
        return frozenset()

    names: set[str] = set()
    for line in result.stdout.splitlines():
        # "plugin:  elementname: Human description"
        parts = line.split(":")
        if len(parts) >= 2:
            names.add(parts[1].strip())
    return frozenset(names)


def choose_encoder(elements: frozenset[str]) -> tuple[str, str, str] | None:
    """The best available `(encoder, muxer, extension)`, or None if nothing can encode."""
    for encoder, muxer, extension in ENCODERS:
        if encoder in elements and muxer in elements:
            return encoder, muxer, extension
    return None


def portal_capabilities() -> tuple[int, int] | None:
    """``(version, available_source_types)`` from the screen-cast portal, or None if absent.

    Reads two D-Bus properties and nothing else. Deliberately does *not* create a session — that
    would put a permission dialog on screen every time the health endpoint is called.
    """
    if not dbus_client_available():
        return None

    try:
        from jeepney import DBusAddress, Properties
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        return None

    address = DBusAddress(
        object_path="/org/freedesktop/portal/desktop",
        bus_name="org.freedesktop.portal.Desktop",
        interface="org.freedesktop.portal.ScreenCast",
    )

    try:
        with open_dbus_connection(bus="SESSION") as connection:
            version = connection.send_and_get_reply(Properties(address).get("version"))
            sources = connection.send_and_get_reply(Properties(address).get("AvailableSourceTypes"))
    except Exception:  # noqa: BLE001 - no session bus, no portal, or a refused property
        logger.debug("The screen-cast portal did not answer", exc_info=True)
        return None

    try:
        return int(version.body[0][1]), int(sources.body[0][1])
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def detect() -> CaptureSupport:
    """The full verdict. Never raises."""
    try:
        return _detect()
    except Exception:  # noqa: BLE001 - a diagnostic must never break the endpoint that reports it
        logger.exception("Capture probe failed")
        return CaptureSupport(
            available=False,
            missing="probe-failed",
            reason="Could not work out whether window capture is available on this machine.",
        )


def _detect() -> CaptureSupport:
    kind = session_type()
    if not kind:
        return CaptureSupport(
            available=False,
            missing="no-display",
            reason=(
                "There is no graphical session here, so there are no windows to capture. "
                "Window recording needs a desktop."
            ),
        )

    if not dbus_client_available():
        return CaptureSupport(
            available=False,
            missing="dbus-client",
            reason="The D-Bus client is missing. Reinstall the dependencies with: uv sync",
            session_type=kind,
        )

    capabilities = portal_capabilities()
    if capabilities is None:
        return CaptureSupport(
            available=False,
            missing="portal",
            reason=(
                "This desktop has no screen-sharing portal (xdg-desktop-portal). "
                "Install the one for your desktop — xdg-desktop-portal-kde, -gnome, or -wlr."
            ),
            session_type=kind,
        )

    version, sources = capabilities
    if not sources & SOURCE_WINDOW:
        return CaptureSupport(
            available=False,
            missing="no-window-source",
            reason=(
                "This desktop's screen sharing can share a whole monitor but not a single window. "
                "Nothing can be done from here — it is a limitation of the compositor."
            ),
            session_type=kind,
            portal_version=version,
        )

    if shutil.which("gst-launch-1.0") is None:
        return CaptureSupport(
            available=False,
            missing="gstreamer",
            reason=(
                "GStreamer is not installed. On Fedora: "
                "sudo dnf install gstreamer1 gstreamer1-plugins-good gstreamer1-plugins-base"
            ),
            session_type=kind,
            portal_version=version,
        )

    elements = gstreamer_elements()
    absent = [name for name in REQUIRED_ELEMENTS if name not in elements]
    if absent:
        return CaptureSupport(
            available=False,
            missing="gst-elements",
            reason=(
                f"GStreamer is missing {', '.join(absent)}. "
                "Install gstreamer1-plugins-good and gstreamer1-plugins-base."
            ),
            session_type=kind,
            portal_version=version,
            missing_elements=absent,
        )

    chosen = choose_encoder(elements)
    if chosen is None:
        return CaptureSupport(
            available=False,
            missing="encoder",
            reason=(
                "GStreamer has no video encoder this can use. Install gstreamer1-plugins-good "
                "for VP8, or gstreamer1-plugins-ugly for H.264."
            ),
            session_type=kind,
            portal_version=version,
            missing_elements=[name for name, _, _ in ENCODERS],
        )

    encoder, muxer, extension = chosen
    return CaptureSupport(
        available=True,
        session_type=kind,
        portal_version=version,
        encoder=encoder,
        muxer=muxer,
        extension=extension,
        # A missing preview costs the monitor its picture and costs the recording nothing.
        preview=PREVIEW_ELEMENT in elements,
    )
