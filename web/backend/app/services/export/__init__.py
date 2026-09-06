"""Exporting a recording as something that opens on its own, at a size worth sending.

``services/transcript/export.py`` renders a transcript as text, Markdown, SRT, VTT, or JSON — five
formats, none of which carry the video and none of which can be asked a question. This package is
the sixth: a folder that opens in a browser with no server and no network, holding the recording,
a transcript that follows it, and a panel that asks a local language model about both.

* ``payload``  — the JSON documents the exported page reads. The conversation is not one of them
                 unless it is asked for (D-037)
* ``webapp``   — assembling the ZIP
* ``profile``  — what a finished recording actually is, measured, and what a re-encode would be
* ``presets``  — the named plans, mirrored into the frontend
* ``estimate`` — what a plan will cost, before anyone commits to it
* ``encode``   — the ffmpeg invocation, with real progress
* ``job``      — one export as stages you can watch
* ``runner``   — driving those stages on a background thread
"""

from .encode import EncodeError, build_command, encode
from .estimate import Estimate, estimate, package_seconds
from .job import ExportJob, ExportRegistry, ExportStage, ExportState, StageState
from .payload import chat_payload, settings_payload, transcript_payload
from .presets import DEFAULT_PRESET, PRESETS, by_id
from .profile import EncodePlan, SourceProfile, probe
from .runner import ExportRunner
from .webapp import ExportError, build_webapp

__all__ = [
    "DEFAULT_PRESET",
    "PRESETS",
    "EncodeError",
    "EncodePlan",
    "Estimate",
    "ExportError",
    "ExportJob",
    "ExportRegistry",
    "ExportRunner",
    "ExportStage",
    "ExportState",
    "SourceProfile",
    "StageState",
    "build_command",
    "build_webapp",
    "by_id",
    "chat_payload",
    "encode",
    "estimate",
    "package_seconds",
    "probe",
    "settings_payload",
    "transcript_payload",
]
