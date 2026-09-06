"""Capturing a window, under a compositor that will not let us just take one (D-022).

* ``probe``    — whether this machine can, and exactly which piece is missing when it cannot
* ``portal``   — the xdg-desktop-portal ScreenCast session: consent, and a PipeWire node
* ``pipeline`` — the GStreamer launch line, built from what is actually installed
* ``recorder`` — the subprocess, its lifetime, and its finalisation
* ``mux``      — combining the video with the session's audio once both are closed
* ``stitch``   — joining the pieces of a capture that had to be restarted, onto one timeline

**Under Wayland an application cannot enumerate windows or read another window's pixels.** The only
sanctioned route is the portal: the compositor shows its own picker, the user consents, and we
receive a PipeWire node we may read. That puts a dialog that can be declined in the middle of the
feature, and it means this application never learns what windows exist.
"""

from .mux import MuxResult, has_both_streams
from .mux import combine as mux_audio_video
from .mux import probe_duration as probe_video_duration
from .pipeline import PipelineSpec
from .pipeline import build as build_pipeline
from .portal import (
    PortalDeclined,
    PortalError,
    PortalSession,
    PortalUnavailable,
    WindowStream,
    open_window_stream,
)
from .probe import CaptureSupport, detect, gstreamer_elements, session_type
from .recorder import RecorderError, RecorderState, WindowRecorder
from .stitch import CaptureSegment, StitchResult
from .stitch import stitch as stitch_segments

__all__ = [
    "CaptureSegment",
    "CaptureSupport",
    "MuxResult",
    "PipelineSpec",
    "PortalDeclined",
    "PortalError",
    "PortalSession",
    "PortalUnavailable",
    "RecorderError",
    "RecorderState",
    "StitchResult",
    "WindowRecorder",
    "WindowStream",
    "build_pipeline",
    "detect",
    "gstreamer_elements",
    "has_both_streams",
    "mux_audio_video",
    "probe_video_duration",
    "open_window_stream",
    "session_type",
    "stitch_segments",
]
