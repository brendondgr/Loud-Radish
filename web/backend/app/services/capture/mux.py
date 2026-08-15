"""Combine the recorded video with the session's audio (D-022).

The two are captured separately and deliberately so. The screen-cast portal carries **video only**,
and putting audio through the same GStreamer pipeline would mean moving capture off the tested path
that feeds the speech model — for a benefit nobody watching a seminar recording will notice.

So they are muxed afterwards, with `ffmpeg`, which is the one job on this machine ffmpeg *can* do:
it has no `pipewiregrab` to consume the portal's stream, but remuxing two finished files is exactly
what it is for.

**Both originals survive until the muxed file exists and is non-empty.** A mux that half-worked and
deleted its inputs would turn a recoverable disappointment into a lost recording.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

#: Generous, because this is copying streams rather than re-encoding: a two-hour recording remuxes
#: in seconds. A timeout this long only fires when something is genuinely stuck.
MUX_TIMEOUT_S: Final = 300.0


@dataclass(frozen=True)
class MuxResult:
    """What came of the attempt."""

    ok: bool
    path: str = ""
    reason: str = ""


def available() -> bool:
    """Whether ffmpeg is here to do it."""
    return shutil.which("ffmpeg") is not None


def combine(
    video_path: str | Path, audio_path: str | Path, *, keep_sources: bool = False
) -> MuxResult:
    """Write one file containing both streams, next to the video.

    Never raises. Failing to mux costs a convenience — two files instead of one — and both are
    still playable on their own, so it must not be able to fail a recording.
    """
    video = Path(video_path)
    audio = Path(audio_path)

    if not available():
        return MuxResult(False, reason="ffmpeg is not installed, so the two files are kept apart.")
    if not video.is_file() or video.stat().st_size == 0:
        return MuxResult(False, reason="There is no video to combine.")
    if not audio.is_file() or audio.stat().st_size == 0:
        return MuxResult(False, reason="There is no audio to combine.")

    output = video.with_name(f"{video.stem}-with-audio{video.suffix}")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
        # Video is copied rather than re-encoded: it was just encoded, and doing it twice costs
        # minutes of CPU and quality for nothing.
        "-c:v",
        "copy",
        # Audio is not copied. The recording is 16 kHz PCM, which WebM cannot carry — Opus is the
        # codec the container expects and is smaller besides.
        "-c:a",
        "libopus",
        "-b:a",
        "64k",
        # Stop at whichever stream ends first. The video ends early whenever the captured window
        # was closed, and without this the file gets a long tail of audio over a frozen frame.
        "-shortest",
        str(output),
    ]

    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, argv built here, no shell
            command, capture_output=True, text=True, timeout=MUX_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return MuxResult(False, reason=f"Combining the audio and video failed: {exc}")

    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        detail = (result.stderr or "").strip().splitlines()
        output.unlink(missing_ok=True)
        return MuxResult(
            False,
            reason=f"Combining the audio and video failed: {detail[-1] if detail else 'unknown'}",
        )

    logger.info("Combined audio and video into %s", output.name)

    # Only now, with a file that exists and has bytes in it. Removing the sources any earlier turns
    # a partial failure into a lost recording.
    if not keep_sources:
        video.unlink(missing_ok=True)

    return MuxResult(True, path=str(output))
