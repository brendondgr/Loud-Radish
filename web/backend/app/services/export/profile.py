"""What a recording actually is, measured — and what a re-encode of it would be (D-037).

Reported: "a roughly 30-minute recording came out at about 100 MB. That's too large to share
comfortably." The measurement behind that report says why, and says it in a way the capture settings
alone could not have predicted: the session asked for `capture.max_height: 720` and recorded at
**2560 × 1532**, because `_record_scaler` omits the scaler entirely whenever the portal reports no
source geometry — which its own comment records as the normal case on this desktop.

**So resolution is decided here, at export, rather than at capture.** Not because capture should not
try, but because at this end the real dimensions are a fact read off a finished file rather than a
hope handed to a compositor. A setting that is silently ignored is worse than no setting.

Two things live in this module. :class:`SourceProfile` is the finished recording as `ffprobe` sees
it. :class:`EncodePlan` is a proposal: a container, a codec, a quality, and ceilings on height and
frame rate. Neither encodes anything — `encode.py` does that, and `estimate.py` predicts what it
will cost before anyone commits to it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

#: Reading a container header. Short: a probe that hangs must not hold up an export dialog.
PROBE_TIMEOUT_S: Final = 20.0


@dataclass(frozen=True)
class SourceProfile:
    """A finished recording, as measured rather than as configured."""

    path: str = ""
    width: int = 0
    height: int = 0
    frame_rate: float = 0.0
    duration_s: float = 0.0
    size_bytes: int = 0
    video_codec: str = ""
    audio_codec: str = ""
    #: Empty when the file was read. Otherwise says what could not be.
    problem: str = ""

    @property
    def readable(self) -> bool:
        return not self.problem and self.duration_s > 0.0

    @property
    def has_video(self) -> bool:
        return bool(self.video_codec) and self.width > 0 and self.height > 0

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_codec)

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def bitrate(self) -> float:
        """Bits per second across the whole file, video and audio together."""
        if self.duration_s <= 0.0:
            return 0.0
        return self.size_bytes * 8 / self.duration_s

    def as_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "frame_rate": round(self.frame_rate, 3),
            "duration_s": round(self.duration_s, 2),
            "size_bytes": self.size_bytes,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "problem": self.problem,
        }


@dataclass(frozen=True)
class EncodePlan:
    """A proposal for what to turn a recording into.

    ``height`` and ``frame_rate`` are **ceilings, never targets**. A window recorded at 640 × 360 is
    already smaller than every preset's ceiling and is left alone; scaling it up to meet a number
    would spend minutes producing a larger file that shows less.
    """

    #: Stable identifier, mirrored into the frontend and held identical by a test.
    id: str
    label: str
    #: What it is for, in the words someone choosing would use.
    hint: str
    container: str = "mp4"
    video_codec: str = "h264"
    audio_codec: str = "aac"
    #: Constant-quality level. Lower is better; the scale is the codec's own CRF.
    crf: int = 26
    #: Ceiling on the encoded height. Zero means "whatever the source is".
    max_height: int = 720
    #: Ceiling on the frame rate. Zero means "whatever the source is".
    max_frame_rate: float = 15.0
    audio_bitrate_k: int = 96
    #: No video track at all. For when the picture does not matter and the talk does.
    audio_only: bool = False
    #: Copy the streams rather than re-encoding. Exact, instant, and the current behaviour.
    copy: bool = False

    def resolved_height(self, source: SourceProfile) -> int:
        if self.audio_only or not source.has_video:
            return 0
        if self.copy or not self.max_height:
            return source.height
        return min(source.height, self.max_height) or source.height

    def resolved_width(self, source: SourceProfile) -> int:
        height = self.resolved_height(source)
        if not height or not source.height:
            return 0
        if height == source.height:
            return source.width
        # Even, because every codec here refuses odd dimensions on 4:2:0 chroma.
        return max(2, round(source.width * height / source.height / 2) * 2)

    def resolved_frame_rate(self, source: SourceProfile) -> float:
        if self.audio_only or not source.has_video:
            return 0.0
        if self.copy or not self.max_frame_rate:
            return source.frame_rate
        return min(source.frame_rate, self.max_frame_rate) or source.frame_rate

    def extension(self, source: SourceProfile) -> str:
        if self.audio_only:
            return "m4a" if self.audio_codec == "aac" else "opus"
        if self.copy:
            return Path(source.path).suffix.lstrip(".") or self.container
        return self.container

    def as_dict(self, source: SourceProfile | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "hint": self.hint,
            "container": self.container,
            "video_codec": "" if self.audio_only else self.video_codec,
            "audio_codec": self.audio_codec,
            "crf": self.crf,
            "max_height": self.max_height,
            "max_frame_rate": self.max_frame_rate,
            "audio_only": self.audio_only,
            "copy": self.copy,
        }
        if source is not None:
            payload["output"] = {
                "width": self.resolved_width(source),
                "height": self.resolved_height(source),
                "frame_rate": round(self.resolved_frame_rate(source), 3),
                "extension": self.extension(source),
            }
        return payload


def probe(path: str | Path) -> SourceProfile:
    """Measure a finished recording. Never raises; an unreadable file says so in ``problem``."""
    target = Path(path)
    if shutil.which("ffprobe") is None:
        return SourceProfile(path=str(target), problem="ffprobe is not installed.")
    try:
        size = target.stat().st_size
    except OSError as exc:
        return SourceProfile(path=str(target), problem=f"Cannot be read ({type(exc).__name__}).")

    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, argv built here, no shell
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height,avg_frame_rate",
                "-of",
                "json",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return SourceProfile(
            path=str(target), size_bytes=size, problem=f"Could not be probed: {exc}"
        )

    if result.returncode != 0:
        return SourceProfile(
            path=str(target), size_bytes=size, problem="Not a media file this machine can read."
        )

    try:
        parsed = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return SourceProfile(path=str(target), size_bytes=size, problem="Its header did not parse.")

    duration = _float(parsed.get("format", {}).get("duration"))
    video = _stream(parsed, "video")
    audio = _stream(parsed, "audio")

    return SourceProfile(
        path=str(target),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        frame_rate=_rate(video.get("avg_frame_rate")),
        duration_s=duration,
        size_bytes=size,
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=str(audio.get("codec_name") or ""),
    )


def _stream(parsed: dict[str, Any], kind: str) -> dict[str, Any]:
    for stream in parsed.get("streams", []):
        if stream.get("codec_type") == kind:
            return stream
    return {}


def _float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _rate(value: Any) -> float:
    """``avg_frame_rate`` arrives as ``"15/1"``, and as ``"0/0"`` when it is unknown."""
    if not value:
        return 0.0
    try:
        rate = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return 0.0
    return rate if rate > 0 else 0.0
