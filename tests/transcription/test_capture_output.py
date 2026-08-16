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


#: A pixel-aspect-ratio extreme enough to overflow the scaler's fixation arithmetic. This is the
#: reproduction of the fault that took window capture from "wrong size" to "does not run at all":
#: asked to hit a target while preserving display aspect, `videoscale` computes
#: ``width * par_num * ...`` and, with a denominator this large, the result does not fit. GStreamer
#: reports ``Error calculating the output scaled size - integer overflow`` and refuses to preroll,
#: which is verbatim what the recorder's log showed.
HOSTILE_PAR = "1/2147483647"


def run_hostile(spec) -> subprocess.CompletedProcess:
    """Run a spec against a source whose aspect ratio breaks the scaler's arithmetic.

    Returns the completed process rather than failing on a non-zero exit, because whether it
    survived is the assertion.
    """
    args = list(spec.args)
    source = args.index("pipewiresrc")
    end = args.index("!", source)
    args[source:end] = [
        "videotestsrc",
        f"num-buffers={FRAMES}",
        "pattern=smpte",
        "!",
        f"video/x-raw,width=1920,height=1080,framerate=15/1,pixel-aspect-ratio={HOSTILE_PAR}",
    ]
    return subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)


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


def test_a_hostile_aspect_ratio_does_not_kill_the_pipeline(tmp_path) -> None:
    """The fault that took window capture from "wrong size" to "does not run at all".

    Reported from the recorder's own log: `Error calculating the output scaled size - integer
    overflow`, `assertion 'denom > 0' failed`, `pipeline doesn't want to preroll` — and on disk, a
    **16x16** recording beside a **480x32767** preview, 32767 being SHRT_MAX. Every one of those is
    the same arithmetic failing: `videoscale` asked to hit a target while preserving display aspect,
    against a source whose pixel-aspect-ratio makes the product overflow.

    Nothing is asked to fixate any more — the preview names two concrete numbers and letterboxes,
    and the recording branch has no scaler at all unless both source dimensions are known — so this
    must simply run. Verified to fail before the change and pass after, against this same source.
    """
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        # Exactly what the portal gives us on this desktop: no size at all.
        source_width=0,
        source_height=0,
    )
    result = run_hostile(spec)

    assert result.returncode == 0, result.stderr
    assert "overflow" not in result.stderr.lower()
    assert "negotiation" not in result.stderr.lower()

    width, height = dimensions(tmp_path / "out.webm")
    assert (width, height) == (1920, 1080), "the source size, unscaled, because it was not known"
    assert dimensions(tmp_path / "preview.jpg") == (
        pipeline.PREVIEW_WIDTH,
        pipeline.PREVIEW_HEIGHT,
    )


def test_the_preview_is_always_the_same_two_numbers(tmp_path) -> None:
    """Fixing both dimensions is what makes the preview branch incapable of the overflow.

    Width alone leaves the height to be fixated from the source's aspect, which is the arithmetic
    that failed. Letterboxing is the price and it is worth paying for a thumbnail.
    """
    spec = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        source_width=0,
        source_height=0,
    )
    assert "add-borders=true" in spec.args
    run_pipeline(spec, width=640, height=480)

    # A 4:3 source into a 16:9 preview: the shape is preserved by bars, not by stretching.
    assert dimensions(tmp_path / "preview.jpg") == (
        pipeline.PREVIEW_WIDTH,
        pipeline.PREVIEW_HEIGHT,
    )


def test_an_unknown_size_is_recorded_rather_than_guessed_at(tmp_path) -> None:
    """No scaler at all when the size is unknown, because every guess so far has been wrong."""
    args = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        want_preview=False,
        source_width=0,
        source_height=0,
    ).args

    assert "videoscale" not in args


def test_a_source_under_the_ceiling_is_not_scaled_either(tmp_path) -> None:
    """Scaling down a window that is already small is work with a cost and no benefit."""
    args = pipeline.build(
        support(),
        node_id=0,
        fd=0,
        video_path=str(tmp_path / "out.webm"),
        preview_path=str(tmp_path / "preview.jpg"),
        want_preview=False,
        source_width=640,
        source_height=480,
        max_height=720,
    ).args

    assert "videoscale" not in args


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


# -- geometry ------------------------------------------------------------------------------------
#
# The size is now decided in `capture/geometry.py` from negotiated caps rather than negotiated by
# the scaler. These check the decision; the tests above check the file it produces.


def test_a_source_under_the_ceiling_keeps_its_own_size() -> None:
    from app.services.capture.geometry import resolve

    geometry = resolve(1080, 1064, max_height=1080)

    assert (geometry.width, geometry.height) == (1080, 1064)
    assert geometry.scaled is False


def test_an_odd_source_is_rounded_down_to_even() -> None:
    """Mandatory for 4:2:0 chroma, and VA-API encoders are stricter about it than software ones.

    The current recordings are even by luck — a window happened to be 1080x1064. Window sizes are
    arbitrary, and this is the trap that bites the moment the encoder changes.
    """
    from app.services.capture.geometry import resolve

    geometry = resolve(1081, 1063, max_height=2160)

    assert (geometry.width, geometry.height) == (1080, 1062)
    assert geometry.scaled is True


def test_a_tall_source_is_capped_and_keeps_its_shape() -> None:
    from app.services.capture.geometry import resolve

    geometry = resolve(2560, 1440, max_height=720)

    assert (geometry.width, geometry.height) == (1280, 720)
    assert geometry.width % 2 == 0 and geometry.height % 2 == 0


@pytest.mark.parametrize(
    ("width", "height"),
    [(0, 0), (1920, 0), (-1, 100), (100_000, 100), (1920, 32767), (8, 8)],
)
def test_a_degenerate_size_is_refused_rather_than_recorded(width: int, height: int) -> None:
    """Values this application has produced, or would produce from a failed negotiation.

    A zero is what the portal reports for a window. 32767 is SHRT_MAX and is what the scaler's
    overflow resolved to. Each would give a plausible file from an implausible number, so the
    number is refused before it can reach an encoder.
    """
    from app.services.capture.geometry import GeometryError, resolve

    with pytest.raises(GeometryError):
        resolve(width, height, max_height=720)


def test_resolving_is_idempotent() -> None:
    """It is called again on every renegotiation, and must not creep by a pixel each time."""
    from app.services.capture.geometry import resolve

    first = resolve(2560, 1440, max_height=720)
    second = resolve(first.width, first.height, max_height=720)

    assert (second.width, second.height) == (first.width, first.height)


def test_an_extreme_aspect_ratio_is_recorded_rather_than_refused() -> None:
    """480x16 was the shape of the original fault, and it is *not* what this guard is for.

    That file was a bad *output* — a scaler resolving a caps range against a preview's width. The
    size now comes from negotiated caps, so a 480x16 source means the compositor really handed over
    something 480x16, and a desktop panel is exactly that: a legitimate window with an extreme
    aspect ratio. Refusing it would decline to record a real window in order to guard against a bug
    that this design has already made impossible.
    """
    from app.services.capture.geometry import resolve

    geometry = resolve(480, 16, max_height=720)

    assert (geometry.width, geometry.height) == (480, 16)
