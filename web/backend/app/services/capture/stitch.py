"""Join the pieces of a capture that had to be restarted, onto one timeline (D-036).

A window capture that dies mid-session is reopened and resumed, and each attempt writes its own
file — ``video.webm``, ``video.002.webm``, and so on — because appending to a finalised container
is not something that can be done safely. What comes back here is those pieces plus the
session-relative second each one *started at*, and what goes out is one video whose clock matches
the audio's.

**The gaps are held, never closed up.** The seconds between one segment ending and the next
beginning are seconds in which nothing was captured, and they are also seconds in which the talk
carried on and the transcript kept being written. Dropping them would shorten the video by exactly
the amount the recording failed for, which slides every frame after the first gap out of sync with
every timestamp in every export — the fault this whole subsystem exists to avoid. So each gap
becomes filler of its own length: the last frame of the segment before it, held.

**The real footage is copied, never re-encoded.** Filling gaps by re-encoding the whole timeline
would cost an hour of CPU to repair fifteen minutes of it, and would lose quality in the parts that
were never broken. Only the filler is encoded — a still image, which is nearly all skipped
macroblocks and costs almost nothing — and everything is then joined with ffmpeg's concat demuxer
as a stream copy. That works only while every piece shares a codec, resolution and frame rate,
which they do: a resumed capture rebuilds the identical pipeline.

**It refuses rather than guesses.** A segment whose duration cannot be read, a filler that will not
encode, a concat that fails — each leaves the pieces exactly where they are and says so. The pieces
play individually, and a caller that got a partial answer would combine them wrongly.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from .mux import probe_duration

logger = logging.getLogger(__name__)

#: Generous: the only re-encoding is of still frames, and the join itself is a copy.
STITCH_TIMEOUT_S: Final = 900.0

#: Gaps below this are inside the measurement's own error and are not worth a filler clip.
MIN_GAP_S: Final = 0.25

#: A gap longer than this is not a resumed capture, it is a number that has gone wrong. Filling one
#: would write hours of held frame; the pieces are left alone instead.
MAX_GAP_S: Final = 4 * 60 * 60.0


@dataclass(frozen=True)
class CaptureSegment:
    """One piece of a capture, and when in the session it began."""

    path: Path
    #: Seconds after the session's audio started that this piece's first frame was captured.
    starts_at_s: float


@dataclass
class StitchResult:
    """What came of joining them."""

    ok: bool
    path: str = ""
    reason: str = ""
    #: Seconds of held frame written to cover the gaps between pieces.
    filled_s: float = 0.0
    #: How many pieces went in. One means nothing was joined and the piece was left as it was.
    segments: int = 0
    #: The pieces that were consumed, so a caller can decide whether to keep them.
    sources: list[Path] = field(default_factory=list)


def available() -> bool:
    """Whether ffmpeg is here to do it."""
    return shutil.which("ffmpeg") is not None


def stitch(segments: list[CaptureSegment], output: Path) -> StitchResult:
    """Join ``segments`` into ``output``, holding the last frame across every gap.

    Never raises. A capture that broke once and was recovered must not be lost a second time by the
    step that puts it back together, so every failure path leaves the pieces on disk untouched.
    """
    if not segments:
        return StitchResult(False, reason="There are no pieces to join.")
    if len(segments) == 1:
        return StitchResult(
            True, path=str(segments[0].path), segments=1, sources=[segments[0].path]
        )
    if not available():
        return StitchResult(False, reason="ffmpeg is not installed, so the pieces are kept apart.")

    ordered = sorted(segments, key=lambda segment: segment.starts_at_s)
    durations = [probe_duration(segment.path) for segment in ordered]
    if any(duration <= 0.0 for duration in durations):
        return StitchResult(
            False,
            reason="One of the recorded pieces has no readable duration, so they are kept apart.",
        )

    parts: list[Path] = []
    filled = 0.0
    scratch = output.parent / ".stitch"
    scratch.mkdir(parents=True, exist_ok=True)

    try:
        for index, segment in enumerate(ordered):
            if index:
                gap = segment.starts_at_s - (ordered[index - 1].starts_at_s + durations[index - 1])
                if gap > MAX_GAP_S:
                    return StitchResult(
                        False,
                        reason=(
                            f"The gap between two pieces reads as {gap / 3600:.1f} hours, which is "
                            "a fault rather than a measurement. They are kept apart."
                        ),
                    )
                if gap >= MIN_GAP_S:
                    filler = _hold_last_frame(ordered[index - 1].path, gap, scratch / f"{index}")
                    if filler is None:
                        return StitchResult(
                            False,
                            reason=(
                                "A gap between two pieces could not be filled, so they are "
                                "kept apart."
                            ),
                        )
                    parts.append(filler)
                    filled += gap
            parts.append(segment.path)

        joined = _concat(parts, output)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if not joined:
        return StitchResult(False, reason="Joining the recorded pieces failed.")

    logger.info(
        "Joined %d recorded pieces into %s, holding %.1fs across the gaps.",
        len(ordered),
        output.name,
        filled,
    )
    return StitchResult(
        True,
        path=str(output),
        filled_s=filled,
        segments=len(ordered),
        sources=[segment.path for segment in ordered],
    )


def _hold_last_frame(source: Path, seconds: float, stem: Path) -> Path | None:
    """A clip of ``seconds`` showing ``source``'s final frame, in ``source``'s own container.

    Two passes rather than one, because they answer different questions. The first extracts the
    frame — ``-sseof -1`` seeks a second from the end, which is where a frame reliably is even when
    the container's duration is approximate. The second encodes that still, which is cheap: every
    frame after the first is identical, so the encoder spends almost nothing on it.
    """
    frame = stem.with_suffix(".png")
    clip = stem.with_suffix(source.suffix)
    rate, codec = _stream_shape(source)
    if not rate or not codec:
        return None

    if not _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-sseof",
            "-1",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-update",
            "1",
            str(frame),
        ]
    ):
        return None

    if not _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            rate,
            "-t",
            f"{seconds:.3f}",
            "-i",
            str(frame),
            "-c:v",
            _encoder_for(codec),
            # A keyframe every second, so the concat demuxer has clean cut points and a viewer
            # scrubbing through a gap lands on a picture rather than on nothing.
            "-g",
            "30",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    ):
        return None
    return clip


def _concat(parts: list[Path], output: Path) -> bool:
    """Join with the concat demuxer, copying streams. Requires identical codec and geometry."""
    listing = output.parent / ".stitch" / "parts.txt"
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text(
        "".join(f"file '{path.resolve()}'\n" for path in parts),
        encoding="utf-8",
    )
    return (
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-c",
                "copy",
                str(output),
            ]
        )
        and output.is_file()
        and output.stat().st_size > 0
    )


def _stream_shape(path: Path) -> tuple[str, str]:
    """``(frame rate, codec name)`` for the video stream, or two empty strings."""
    if shutil.which("ffprobe") is None:
        return "", ""
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, argv built here, no shell
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate,codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(values) < 2:
        return "", ""
    codec, rate = values[0], values[1]
    return (rate, codec) if rate not in {"0/0", "0/1"} else ("", "")


#: The encoder that writes each codec back. The filler has to be bit-compatible with the real
#: footage for a stream copy to join them, which means the same codec and nothing else.
_ENCODERS: Final[dict[str, str]] = {
    "vp8": "libvpx",
    "vp9": "libvpx-vp9",
    "h264": "libx264",
    "hevc": "libx265",
    "av1": "libsvtav1",
}


def _encoder_for(codec: str) -> str:
    return _ENCODERS.get(codec, codec)


def _run(command: list[str]) -> bool:
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, argv built here, no shell
            command, capture_output=True, text=True, timeout=STITCH_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Stitching step failed: %s", exc)
        return False
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        logger.warning("Stitching step failed: %s", detail[-1] if detail else "unknown")
        return False
    return True
