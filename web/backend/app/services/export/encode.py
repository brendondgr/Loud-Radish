"""Re-encode a finished recording to an :class:`EncodePlan`, reporting progress as it goes (D-037).

One `ffmpeg` invocation, built here and run by `runner.py`. Two things about it are worth stating.

**The progress is real, not a spinner.** `-progress pipe:1` makes ffmpeg emit a block of key-value
lines every second — among them `out_time_us`, the position in the output it has reached. Divided by
the recording's known duration that is an honest fraction, monotonic, and needing no instrumentation
inside the encoder. It is the same shape of answer the transcription pass gives, and for the same
reason: progress by *position in the material* is the only kind that cannot lie.

**A copy is not an encode.** The `original` plan streams both tracks through untouched, which takes
seconds for an hour of video rather than minutes, and produces exactly the bytes that were recorded.
It is offered first in the preset list because it is what the export has always done.

The output is written beside the recording, not into a temporary directory, so an export that is
interrupted leaves a partial file where it can be seen and removed rather than in `/tmp` where it
cannot.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

from .profile import EncodePlan, SourceProfile

logger = logging.getLogger(__name__)

#: An hour of 1080p at `veryfast` is minutes, not tens of minutes. This is the ceiling above which
#: something has gone wrong rather than slowly.
ENCODE_TIMEOUT_S: Final = 4 * 60 * 60.0

#: How many of ffmpeg's own stderr lines to keep when it fails. One useful paragraph, not a log.
STDERR_LIMIT: Final = 4_000

#: What writes each codec. `libx264` and the rest are present in this machine's ffmpeg even though
#: GStreamer's equivalents are not, which is why the export can offer H.264 when capture cannot.
ENCODERS: Final[dict[str, str]] = {
    "h264": "libx264",
    "hevc": "libx265",
    "vp9": "libvpx-vp9",
    "av1": "libsvtav1",
}

AUDIO_ENCODERS: Final[dict[str, str]] = {
    "aac": "aac",
    "opus": "libopus",
}


class EncodeError(RuntimeError):
    """The re-encode could not be run, or ran and failed. The message says which."""


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_command(plan: EncodePlan, source: SourceProfile, output: Path) -> list[str]:
    """The exact argv.

    Separated from running it so a test can read the decision without spending an encode on it.
    """
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        source.path,
        "-progress",
        "pipe:1",
    ]

    if plan.copy:
        return [*command, "-c", "copy", str(output)]

    if plan.audio_only:
        command += ["-vn"]
    else:
        filters = _filters(plan, source)
        if filters:
            command += ["-vf", ",".join(filters)]
        command += [
            "-c:v",
            ENCODERS.get(plan.video_codec, plan.video_codec),
            "-crf",
            str(plan.crf),
            # 4:2:0, because a screen capture arrives as RGB and every player expects this.
            "-pix_fmt",
            "yuv420p",
        ]
        # `veryfast` for x264 and x265 rather than the default: the measured difference against
        # `medium` on this content was under three per cent of file size for three times the CPU,
        # and this runs on a machine that may also be transcribing. The other encoders take a
        # different vocabulary for the same idea, which `_speed` supplies.
        if plan.video_codec in {"h264", "hevc"}:
            command += ["-preset", "veryfast"]
        command += _speed(plan)
        if plan.container == "mp4":
            # The index at the front, so the file starts playing before it has finished downloading
            # — which is what "I sent them the video" usually means in practice.
            command += ["-movflags", "+faststart"]

    if source.has_audio:
        command += [
            "-c:a",
            AUDIO_ENCODERS.get(plan.audio_codec, plan.audio_codec),
            "-b:a",
            f"{plan.audio_bitrate_k}k",
        ]
    else:
        command += ["-an"]

    return [*command, str(output)]


def _speed(plan: EncodePlan) -> list[str]:
    """Speed settings for the encoders that do not take `-preset`.

    **libvpx-vp9 needs this and libx264 does not.** Measured on the same 120-second slice, at the
    same output size: `libx264 -preset veryfast` took 1.5 s and `libvpx-vp9` at its defaults took
    **448 s** — three hundred times longer, and seven wall-clock minutes for every two minutes of
    talk. An hour of seminar would have been most of a working afternoon, offered from a dropdown
    beside options that take twenty seconds, with an estimate that said twenty seconds.

    `-deadline good -cpu-used 4` with row multithreading is the setting that makes VP9 a choice
    rather than a trap. `-row-mt 1` is what lets it use more than a couple of cores at all.
    """
    if plan.video_codec == "vp9":
        return ["-deadline", "good", "-cpu-used", "4", "-row-mt", "1", "-tile-columns", "2"]
    if plan.video_codec == "av1":
        return ["-preset", "8"]
    return []


def _filters(plan: EncodePlan, source: SourceProfile) -> list[str]:
    """The scale and frame-rate filters, added only where they change something.

    **Ceilings, not targets.** A window recorded at 640 x 360 is already under every preset's
    ceiling; scaling it up to meet one would spend minutes producing a larger file that shows less.
    Same for the frame rate: `fps` on a source already slower than the ceiling duplicates frames.
    """
    filters: list[str] = []
    height = plan.resolved_height(source)
    if height and height != source.height:
        # `-2` keeps the aspect ratio and rounds to an even width, which 4:2:0 chroma requires.
        filters.append(f"scale=-2:{height}")

    rate = plan.resolved_frame_rate(source)
    if rate and source.frame_rate and rate < source.frame_rate - 0.01:
        filters.append(f"fps={rate:g}")
    return filters


def encode(
    plan: EncodePlan,
    source: SourceProfile,
    output: Path,
    *,
    on_progress: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Path:
    """Run the encode, calling ``on_progress`` with a fraction between 0 and 1.

    Raises:
        EncodeError: when ffmpeg is missing, will not start, is cancelled, or exits non-zero.
    """
    if not available():
        raise EncodeError("ffmpeg is not installed, so this recording cannot be re-encoded.")
    if not source.readable:
        raise EncodeError(source.problem or "The recording could not be read.")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(plan, source, output)
    logger.info("Encoding %s -> %s (%s)", Path(source.path).name, output.name, plan.id)

    try:
        process = subprocess.Popen(  # noqa: S603 - argv built from a fixed vocabulary
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise EncodeError(f"Could not start the encoder: {exc}") from exc

    errors: list[str] = []
    # Drained on its own thread. ffmpeg writes progress to stdout and complaints to stderr, and a
    # pipe nobody reads blocks its writer at 64 KB — which would stall the encode as a consequence
    # of it having something to say.
    drain = threading.Thread(
        target=_drain, args=(process, errors), name="encode-stderr", daemon=True
    )
    drain.start()

    cancelled = False
    try:
        for fraction in _progress(process, source.duration_s):
            if on_progress is not None:
                on_progress(fraction)
            if should_stop is not None and should_stop():
                cancelled = True
                process.terminate()
                break
        process.wait(timeout=ENCODE_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        raise EncodeError("The encoder did not finish in time and was stopped.") from exc
    finally:
        drain.join(timeout=5.0)

    if cancelled:
        output.unlink(missing_ok=True)
        raise EncodeError("The export was cancelled.")

    if process.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        detail = " ".join(errors)[-STDERR_LIMIT:].strip()
        raise EncodeError(f"Re-encoding failed: {detail or f'exit code {process.returncode}'}")

    if on_progress is not None:
        on_progress(1.0)
    return output


def _progress(process: subprocess.Popen, duration_s: float):
    """Yield a fraction for every progress block ffmpeg emits.

    `-progress` writes ``key=value`` lines and closes each block with ``progress=continue`` or
    ``progress=end``. Only ``out_time_us`` is read: it is the position reached in the output, which
    against a known duration is an honest fraction.
    """
    stdout = process.stdout
    if stdout is None:
        return
    for line in stdout:
        key, separator, value = line.strip().partition("=")
        if not separator or key != "out_time_us":
            continue
        try:
            seconds = int(value) / 1e6
        except ValueError:
            continue
        if duration_s > 0:
            yield min(1.0, max(0.0, seconds / duration_s))


def _drain(process: subprocess.Popen, errors: list[str]) -> None:
    stream = process.stderr
    if stream is None:
        return
    try:
        for line in stream:
            text = line.strip()
            if text:
                errors.append(text)
                del errors[:-40]
    except (OSError, ValueError):
        return
