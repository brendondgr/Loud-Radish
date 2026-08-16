"""Audio sources — everything that can produce canonical-format frames.

All implement :class:`~.base.AudioSource`, so the session manager wires the pipeline once and never
learns which is running.
"""

from .base import AudioSource, ErrorCallback, FrameCallback, SourceInfo
from .device import DeviceSource, DeviceUnavailableError
from .file import WavFileSource
from .monitor import MonitorSource
from .synthetic import Span, SyntheticSource, silence, speech

__all__ = [
    "AudioSource",
    "DeviceSource",
    "DeviceUnavailableError",
    "ErrorCallback",
    "FrameCallback",
    "MonitorSource",
    "SourceInfo",
    "Span",
    "SyntheticSource",
    "WavFileSource",
    "silence",
    "speech",
]
