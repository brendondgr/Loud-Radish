"""Capturing a window, under a compositor that will not let us just take one (D-022).

* ``probe``    — whether this machine can, and exactly which piece is missing when it cannot
* ``portal``   — the xdg-desktop-portal ScreenCast session: consent, and a PipeWire node
* ``pipeline`` — the GStreamer launch line, built from what is actually installed
* ``recorder`` — the subprocess, its lifetime, and its finalisation
* ``mux``      — combining the video with the session's audio once both are closed

**Under Wayland an application cannot enumerate windows or read another window's pixels.** The only
sanctioned route is the portal: the compositor shows its own picker, the user consents, and we
receive a PipeWire node we may read. That puts a dialog that can be declined in the middle of the
feature, and it means this application never learns what windows exist.
"""

from .probe import CaptureSupport, detect, gstreamer_elements, session_type

__all__ = [
    "CaptureSupport",
    "detect",
    "gstreamer_elements",
    "session_type",
]
