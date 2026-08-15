"""Build the GStreamer launch line from what is actually installed (D-022).

Kept apart from the recorder so the encoder choice is **data rather than branching**: the probe says
what exists, this turns that into a command, and the recorder runs it without knowing which codec
it got. Every combination is then testable without spawning anything.

**GStreamer is driven as a subprocess rather than through PyGObject.** The bindings need system
GObject introspection to build inside a `uv` virtualenv, which is the same class of dependency
D-011 removed from the frontend and for the same reason. `gst-launch-1.0` is a stable command-line
interface to the identical pipeline.

**ffmpeg cannot do this job on this machine.** Consuming the portal's PipeWire node needs the
`pipewiregrab` filter, and this build of ffmpeg 8.1.2 does not have it, while GStreamer's
`pipewiresrc` is present. ffmpeg is still used, later, to mux the finished video with the audio.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .probe import CaptureSupport

#: Ceiling on the encoded height. 720p by default because there is **no hardware encoder on this
#: class of machine** — encoding is software VP8 on the same CPU that runs the speech model, which
#: was measured at a real-time factor around 1.5. Halving the pixel count is the cheapest lever.
DEFAULT_MAX_HEIGHT: Final = 720

#: Frames per second. Fifteen is ample for a talk: slides change every thirty seconds and a cursor
#: moving at 15 fps is perfectly legible. Thirty would double the encoder's work for nothing.
DEFAULT_FRAME_RATE: Final = 15

#: The preview branch's rate. One frame a second is enough to answer "is it still capturing?",
#: which is the only question the monitor is asked.
PREVIEW_FPS: Final = 1
PREVIEW_WIDTH: Final = 480


@dataclass(frozen=True)
class PipelineSpec:
    """A launch line and the files it will produce."""

    args: list[str]
    video_path: str
    preview_path: str

    @property
    def command(self) -> str:
        """The pipeline as one string, for the log. Never executed — `args` is."""
        return " ".join(self.args)


def encoder_args(encoder: str) -> list[str]:
    """Per-encoder tuning, chosen for *not falling behind* rather than for file size.

    A recording that drops frames because the encoder could not keep up is worse than a larger
    file, and this is competing with speech inference for the same cores.
    """
    if encoder == "vp8enc":
        # `deadline=1` is VP8's realtime mode; `cpu-used` trades quality for speed, and `threads`
        # keeps it from taking every core away from the model.
        return ["vp8enc", "deadline=1", "cpu-used=8", "threads=2", "keyframe-max-dist=30"]
    if encoder == "vp9enc":
        return ["vp9enc", "deadline=1", "cpu-used=8", "threads=2"]
    if encoder == "x264enc":
        # `zerolatency` also disables B-frames, which is what makes the file playable while it is
        # still being written.
        return ["x264enc", "speed-preset=veryfast", "tune=zerolatency", "key-int-max=30"]
    if encoder == "openh264enc":
        return ["openh264enc", "complexity=0"]
    return [encoder]


def build(
    support: CaptureSupport,
    *,
    node_id: int,
    fd: int,
    video_path: str,
    preview_path: str,
    frame_rate: int = DEFAULT_FRAME_RATE,
    max_height: int = DEFAULT_MAX_HEIGHT,
    want_preview: bool = True,
    source_width: int = 0,
    source_height: int = 0,
) -> PipelineSpec:
    """Assemble the launch line for one recording.

    Raises:
        ValueError: if the support verdict says this machine cannot encode. Callers check
            ``support.available`` first; this is the guard against one that forgot.
    """
    if not support.encoder or not support.muxer:
        raise ValueError("No usable video encoder was found on this machine.")

    args: list[str] = [
        "gst-launch-1.0",
        "-q",
        # `-e` sends end-of-stream on SIGINT, which is what finalises the container. Without it a
        # stopped recording is a file with no duration index that many players refuse to seek.
        "-e",
        "pipewiresrc",
        f"fd={fd}",
        f"path={node_id}",
        # The portal's node is live: a late frame should be dropped rather than queued, or the
        # recording drifts behind the clock it is being muxed against.
        "do-timestamp=true",
        "!",
        "videorate",
        "drop-only=true",
        "!",
        f"video/x-raw,framerate={frame_rate}/1",
        "!",
        "videoconvert",
        "!",
    ]

    include_preview = want_preview and support.preview

    # **Each branch scales for itself.** A single shared `videoscale` ahead of the tee looks
    # economical and is a trap: GStreamer resolves caps upstream, so the preview's fixed
    # `width=480` propagated back through the tee and became the *recording's* width too. Every
    # window capture on this machine was written at 480 pixels wide, and — with the height left as
    # an open range for the scaler to satisfy however it liked — one was written at **480x16**, a
    # sixteen-pixel-tall strip of a talk. Two scalers cost one extra rescale of a frame that is
    # already being encoded twice; a recording nobody can watch costs the recording.
    if include_preview:
        args += ["tee", "name=t", "!", "queue", "!"]

    args += ["videoscale", "!", _record_caps(max_height, source_width, source_height), "!"]
    args += encoder_args(support.encoder)
    args += ["!", support.muxer, "!", "filesink", f"location={video_path}"]

    if include_preview:
        args += [
            "t.",
            "!",
            "queue",
            "leaky=downstream",
            "max-size-buffers=2",
            "!",
            "videorate",
            "!",
            f"video/x-raw,framerate={PREVIEW_FPS}/1",
            "!",
            "videoscale",
            "!",
            # Width only. Pinning the height as well would letterbox or stretch a window whose
            # shape is not the preview's, and the monitor pane is sized from the image it gets.
            f"video/x-raw,width={PREVIEW_WIDTH},pixel-aspect-ratio=1/1",
            "!",
            "jpegenc",
            "quality=60",
            "!",
            # One file, rewritten each frame. `multifilesink` with `max-files=1` keeps exactly one
            # image on disk rather than accumulating one per second for the length of a talk.
            "multifilesink",
            f"location={preview_path}",
            "max-files=1",
            "post-messages=false",
        ]

    return PipelineSpec(
        args=args,
        video_path=video_path,
        preview_path=preview_path if include_preview else "",
    )


def _record_caps(max_height: int, source_width: int, source_height: int) -> str:
    """The recording branch's output size.

    **Concrete numbers whenever the portal told us the source size**, because a caps *range* asks
    the scaler to pick, and a scaler with a range and any other constraint in play will pick
    something no one wanted — which is exactly how a talk got recorded sixteen pixels tall. When
    the size is unknown the range is still the right answer, but it is now the only constraint on
    the branch, so `videoscale` passes a small window through untouched instead of resolving it
    against a preview's width.

    Only ever scales *down*: a window smaller than the ceiling is sharper at its own size than
    stretched up to one it never reached.
    """
    if source_width > 0 and source_height > 0:
        height = min(source_height, max_height)
        width = max(2, round(source_width * height / source_height))
        # Even dimensions: VP8, VP9 and H.264 all subsample chroma, and an odd edge is either
        # rejected outright or silently rounded by the encoder.
        return f"video/x-raw,width={width - width % 2},height={height - height % 2}"
    return f"video/x-raw,height=[1,{max_height}],pixel-aspect-ratio=1/1"
