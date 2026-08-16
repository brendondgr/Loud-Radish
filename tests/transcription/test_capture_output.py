"""What the capture pipeline actually wrote, read back off the disk.

Every other capture test in this repository asserts the *shape of the command*. That is worth
having and it is not enough: window recording shipped with 1291 tests passing while writing
**480x16** files — a sixteen-pixel-tall strip of a talk — because the launch line was well-formed,
`gst-launch-1.0` exited zero, and nothing anywhere opened the result. The preview branch's fixed
width had propagated upstream through the tee and set the recording's width; the open height range
let the pair resolve to a strip. No warning, no log, no failing test.

So these tests run the **real pipeline** and then open the file. Only the source element is
substituted — `videotestsrc` for `pipewiresrc`, because that one element needs a compositor and a
granted portal session, and everything downstream of it is exactly what a real capture runs. Three
properties are checked, chosen because each catches a fault the command-shape tests cannot see:

* **dimensions** — the 480x16 fault, and any future scaler cross-talk;
* **frame count** — a pipeline that negotiates and then stalls;
* **mean luminance** — a recording of the right size that is entirely black, which is what a
  capture of a dead or wrong node looks like.

They are slower than the rest of the suite by a wide margin. That is the price of checking the
thing itself, and it is why `docs/documentation.md` D-026 records the reasoning: a contributor who
finds a slow test and not its purpose will delete it, and the fault will return.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest
from app.services.capture import pipeline
from app.services.capture.probe import CaptureSupport

pytestmark = pytest.mark.skipif(
    shutil.which("gst-launch-1.0") is None or shutil.which("ffprobe") is None,
    reason="GStreamer and ffmpeg are system packages; without them nothing here can run",
)

#: Enough frames to prove the pipeline runs rather than merely negotiates, and few enough that the
#: whole file is written in about a second.
FRAMES = 20


def support(**overrides) -> CaptureSupport:
    defaults = {
        "available": True,
        "session_type": "wayland",
        "portal_version": 5,
        "encoder": "vp8enc",
        "muxer": "webmmux",
        "extension": "webm",
        "preview": True,
    }
    return CaptureSupport(**{**defaults, **overrides})


def run_pipeline(spec, *, width: int, height: int) -> None:
    """Run a built spec with the portal source swapped for a test pattern.

    The substitution replaces only the elements between ``pipewiresrc`` and the first ``!``. Every
    element after that — the rate, the tee, both scalers, the encoder, the muxer, the sinks — is the
    real thing, which is the entire point.
    """
    args = list(spec.args)
    source = args.index("pipewiresrc")
    end = args.index("!", source)
    args[source:end] = [
        "videotestsrc",
        f"num-buffers={FRAMES}",
        "pattern=smpte",
        "!",
        f"video/x-raw,width={width},height={height},framerate=15/1",
    ]

    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        pytest.fail(f"The pipeline failed: {result.stderr.strip() or result.stdout.strip()}")


def dimensions(path) -> tuple[int, int]:
    """The width and height the file actually has, read with ffprobe."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.strip()
    width, height = (int(part) for part in out.split(",")[:2])
    return width, height


def frame_count(path) -> int:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout.strip()
    return int(out.split(",")[0]) if out and out.split(",")[0].isdigit() else 0


def mean_luminance(path) -> float:
    """Average brightness of the first frame, 0-255.

    A recording of the correct size that is entirely black is what a dead or wrong PipeWire node
    produces, and it is indistinguishable from a working one by every other measure.
    """
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vframes",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        capture_output=True,
        timeout=60,
        check=True,
    ).stdout
    if not raw:
        return 0.0
    return float(np.frombuffer(raw, dtype=np.uint8).mean())


# -- the recording ----------------------------------------------------------------------------


def test_a_capture_is_written_at_the_size_it_was_asked_for(tmp_path) -> None:
    """The regression test for 480x16.

    A 1920x1080 source with a 720 ceiling must produce 1280x720. Before the fix it produced
    480x270, because the preview branch's width propagated back through the tee.
    """
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=1920,
        source_height=1080,
        max_height=720,
    )
    run_pipeline(spec, width=1920, height=1080)

    assert dimensions(tmp_path / "out.webm") == (1280, 720)


def test_the_preview_does_not_shrink_the_recording(tmp_path) -> None:
    """The two files are written by one pipeline and must not agree on a size.

    This is the fault stated as a property rather than as a number: whatever the preview is, the
    recording is not that.
    """
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=1920,
        source_height=1080,
        max_height=720,
    )
    run_pipeline(spec, width=1920, height=1080)

    assert dimensions(tmp_path / "out.webm")[0] != pipeline.PREVIEW_WIDTH
    assert dimensions(tmp_path / "preview.jpg")[0] == pipeline.PREVIEW_WIDTH


def test_a_window_smaller_than_the_ceiling_keeps_its_own_size(tmp_path) -> None:
    """Scaling is only ever downward: stretching a small window up loses sharpness for nothing."""
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=640,
        source_height=480,
        max_height=720,
    )
    run_pipeline(spec, width=640, height=480)

    assert dimensions(tmp_path / "out.webm") == (640, 480)


def test_frames_actually_reach_the_file(tmp_path) -> None:
    """A pipeline can negotiate correctly and then stall, which looks identical from the outside."""
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=640,
        source_height=480,
    )
    run_pipeline(spec, width=640, height=480)

    # Not all FRAMES: the encoder is in realtime mode and the run ends on end-of-stream, so a
    # frame or two at the tail is a normal outcome rather than a fault.
    assert frame_count(tmp_path / "out.webm") >= FRAMES // 2


def test_the_recording_is_not_a_black_rectangle(tmp_path) -> None:
    """What a capture of a dead or wrong node looks like, and nothing else detects it."""
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=640,
        source_height=480,
    )
    run_pipeline(spec, width=640, height=480)

    assert mean_luminance(tmp_path / "out.webm") > 10.0


def test_the_preview_is_a_readable_image(tmp_path) -> None:
    """The monitor pane shows this file; a zero-byte or truncated JPEG reads as a failed capture."""
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=1280,
        source_height=720,
    )
    run_pipeline(spec, width=1280, height=720)

    preview = tmp_path / "preview.jpg"
    assert preview.stat().st_size > 0
    assert dimensions(preview)[1] > 1
    assert mean_luminance(preview) > 10.0


def test_a_capture_without_a_preview_still_records(tmp_path) -> None:
    """The preview is the expendable half; declining it must not change the recording."""
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=1280,
        source_height=720,
        want_preview=False,
    )
    run_pipeline(spec, width=1280, height=720)

    assert dimensions(tmp_path / "out.webm") == (1280, 720)
    assert not (tmp_path / "preview.jpg").exists()
