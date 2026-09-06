"""Combine the recorded video with the session's audio (D-022).

The two are captured separately and deliberately so. The screen-cast portal carries **video only**,
and putting audio through the same GStreamer pipeline would mean moving capture off the tested path
that feeds the speech model — for a benefit nobody watching a seminar recording will notice.

So they are muxed afterwards, with `ffmpeg`, which is the one job on this machine ffmpeg *can* do:
it has no `pipewiregrab` to consume the portal's stream, but remuxing two finished files is exactly
what it is for.

**The silent original survives until the combined file is verified.** Not "exists and is non-empty"
— that passed for a file with a video track and no audio, which is the failure worth catching — and
not "forever" either, which is what it used to mean and which left every window recording holding
the same footage twice, once silent. It goes once the output has been probed and holds both streams.

**The two inputs did not start at the same moment, and the difference is measured rather than
assumed.** Audio capture begins before the screen-cast portal is even asked, deliberately, so that
the first words of a talk are not lost while someone chooses a window from a dialog. The video
therefore starts somewhere between a fraction of a second and however long that dialog was on
screen *later* — 2.1 s on the recording that prompted this — and a mux that lines both inputs up at
zero plays the sound that far ahead of the picture. `video_lag_s` delays the video to meet the
audio.

**The output keeps the audio's timeline, never the video's.** Transcript timestamps are
audio-relative and the exported web application syncs the transcript against this file, so trimming
the audio's head to meet the video would silently invalidate every timestamp in every export. The
recording opens instead on its first frame, held still for as long as the capture took to start,
which is a truthful picture of a moment when nothing was being captured yet.

**And it keeps the whole of both, which is a repair.** This used to pass ``-shortest``, to avoid a
long tail of audio over a frozen frame when the captured window was closed early. Measured against
a 68-minute seminar whose video died at 14 m 43 s: the sound ran to 3925.7 s, the picture to
1765.2 s, and ``-shortest`` cut the output to the picture. The combined file was then probed,
found to hold both a video and an audio track — both truncated, but both present — so the source
video was deleted, and the source WAV went with the transcription pass that had already succeeded.
**Thirty-six minutes of a talk survived every stage of the recording and were destroyed by the one
that tidies up.** A frozen tail is a cosmetic complaint; deleting evidence to answer it is not a
trade worth making, and the tail is in any case a truthful picture of a capture that stopped. So
the output now runs as long as its longer input, and a source is deleted only when the output
demonstrably contains it — same duration, not merely the same kinds of stream.
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

#: Reading a container header. Short on purpose: a probe that hangs must not hold up a recording.
PROBE_TIMEOUT_S: Final = 20.0


@dataclass(frozen=True)
class MuxResult:
    """What came of the attempt."""

    ok: bool
    path: str = ""
    reason: str = ""
    #: Seconds the video was delayed by to meet the audio. Zero when no correction was needed.
    video_lag_s: float = 0.0
    #: Whether the silent original was removed. False when it was kept, and the reason says why.
    removed_source: bool = False
    #: Seconds of audio the output is missing against the source WAV. Non-zero means the video
    #: stopped early, and is what tells the caller the source audio must not be deleted either.
    audio_shortfall_s: float = 0.0
    #: Seconds of video the output is missing against the source video. Non-zero means the mux
    #: itself dropped picture, which is a fault rather than a short recording.
    video_shortfall_s: float = 0.0


#: Offsets below this are not worth correcting and are within the measurement's own error.
MIN_LAG_S: Final = 0.08

#: How much shorter than a source the output may be before it counts as missing something.
#: Container durations disagree with each other by a frame or two as a matter of course — the
#: measured muxes in ``data/recordings/`` differ from their inputs by up to 6 ms — so the threshold
#: is far above that noise and far below any truncation worth worrying about.
DURATION_TOLERANCE_S: Final = 1.0

#: An offset larger than this is not a measurement, it is a fault. Applying one would push the
#: picture minutes away from the sound, which is far worse than the drift it was meant to fix.
MAX_LAG_S: Final = 120.0


def available() -> bool:
    """Whether ffmpeg is here to do it."""
    return shutil.which("ffmpeg") is not None


def _probe(path: Path, entries: str) -> list[str]:
    """Ask ffprobe for one field per stream or for the format. Empty when it cannot be asked."""
    if shutil.which("ffprobe") is None:
        return []
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary, argv built here, no shell
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                entries,
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def probe_duration(path: str | Path) -> float:
    """The encoded length of a media file in seconds, or 0.0 if it cannot be read.

    Used to derive when a recording *started*, from when it was told to stop. That indirection
    exists because the other end is not observable: spawning GStreamer and its first frame reaching
    the encoder are seconds apart on a cold pipeline, and no signal marks the difference.
    """
    values = _probe(Path(path), "format=duration")
    try:
        return max(0.0, float(values[0]))
    except (IndexError, ValueError):
        return 0.0


def has_both_streams(path: str | Path) -> bool:
    """Whether a file holds a video track *and* an audio track.

    The question asked before deleting the silent original. "The output exists and has bytes in it"
    is true of a mux that copied the video and dropped the audio, which is precisely the outcome
    that must not cost the only other copy.
    """
    kinds = set(_probe(Path(path), "stream=codec_type"))
    return {"video", "audio"} <= kinds


def shortfall_against(output: str | Path, source: str | Path) -> float:
    """Seconds of ``source`` the muxed ``output`` does not contain.

    Zero when the output is at least as long, when either duration cannot be read, or when the
    difference is inside :data:`DURATION_TOLERANCE_S`. **An unreadable duration reports zero on
    purpose**: this number guards a deletion, and a probe that could not run must not be what stops
    the tidy-up any more than it may be what causes a loss. The pairing that matters — both files
    readable and the output plainly shorter — is unambiguous.
    """
    out_s = probe_duration(output)
    src_s = probe_duration(source)
    if out_s <= 0.0 or src_s <= 0.0:
        return 0.0
    return max(0.0, src_s - out_s) if src_s - out_s > DURATION_TOLERANCE_S else 0.0


def combine(
    video_path: str | Path,
    audio_path: str | Path,
    *,
    keep_sources: bool = False,
    video_lag_s: float = 0.0,
) -> MuxResult:
    """Write one file containing both streams, next to the video.

    Args:
        video_lag_s: how much later than the audio the video began, in seconds. The video is
            delayed by this much so the two line up, keeping the audio's timeline. Ignored when it
            is below :data:`MIN_LAG_S` — inside the measurement's own error — or above
            :data:`MAX_LAG_S`, where it is a fault rather than a measurement.
        keep_sources: keep the silent original even once the combined file is verified. The
            default removes it, because otherwise every window recording stores the same footage
            twice.

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
    lag = video_lag_s if MIN_LAG_S <= video_lag_s <= MAX_LAG_S else 0.0
    if video_lag_s and not lag:
        logger.info(
            "Not correcting a %.3fs capture offset: outside the %.2f–%.0fs range worth applying.",
            video_lag_s,
            MIN_LAG_S,
            MAX_LAG_S,
        )

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        # The video is shifted, never the audio. `-itsoffset` moves this input's timestamps
        # forward so the picture lands where the sound already is, which keeps the output on the
        # audio's clock — the one the transcript's timestamps are in.
        *(["-itsoffset", f"{lag:.3f}"] if lag else []),
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
        # **No `-shortest`.** The output runs as long as its longer input. When the video ended
        # early the file carries a held frame over the remaining sound, which is what actually
        # happened; truncating to the picture instead deleted thirty-six minutes of a seminar,
        # because the sources go once the output is judged complete.
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

    if lag:
        logger.info(
            "Combined audio and video into %s, delaying the video by %.3fs to meet the audio.",
            output.name,
            lag,
        )
    else:
        logger.info("Combined audio and video into %s", output.name)

    # **Verified, then removed — and verification is now about length as well as kind.** "Exists
    # and has bytes" was already true of a mux that copied the video and silently dropped the
    # audio. "Holds both kinds of stream" was already true of a mux that truncated both to a video
    # that died at a quarter of the way in. What makes a deletion safe is the output containing the
    # source, so that is what is asked.
    audio_short = shortfall_against(output, audio)
    video_short = shortfall_against(output, video)

    removed = False
    if audio_short:
        # Not an error — a video that stopped early is a thing that happens, and the combined file
        # is still correct for as far as the picture goes. It does mean every source stays.
        logger.warning(
            "The combined file is %.1fs shorter than the recorded audio; keeping both sources.",
            audio_short,
        )
    elif video_short:
        logger.warning(
            "The combined file is %.1fs shorter than the recorded video; keeping both sources.",
            video_short,
        )
    elif not keep_sources:
        if has_both_streams(output):
            video.unlink(missing_ok=True)
            removed = True
        else:
            logger.warning(
                "Keeping %s: the combined file does not hold both a video and an audio track.",
                video.name,
            )

    return MuxResult(
        True,
        path=str(output),
        video_lag_s=lag,
        removed_source=removed,
        audio_shortfall_s=audio_short,
        video_shortfall_s=video_short,
    )
