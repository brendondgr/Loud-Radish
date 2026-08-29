"""Exporting a recording as something that opens on its own.

``services/transcript/export.py`` renders a transcript as text, Markdown, SRT, VTT, or JSON — five
formats, none of which carry the video and none of which can be asked a question. This package is
the sixth: a folder that opens in a browser with no server and no network, holding the recording,
a transcript that follows it, and a panel that asks a local language model about both.
"""

from .payload import settings_payload, transcript_payload
from .webapp import ExportError, build_webapp

__all__ = [
    "ExportError",
    "build_webapp",
    "settings_payload",
    "transcript_payload",
]
