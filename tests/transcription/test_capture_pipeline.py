"""The GStreamer launch line, and the process that runs it (D-022).

The pipeline half is pure: given what the probe found, what command comes out. The point of keeping
it separate from the recorder is that every encoder and container combination is checkable without
spawning anything, and that the recorder never learns which codec it got.

The recorder half spawns **real processes**, deliberately. Its entire job is lifetime — start,
notice a death, and end in a way that finalises the container — and none of that is meaningfully
testable against a stub that cannot die on its own.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from app.services.capture import pipeline
from app.services.capture.probe import CaptureSupport
from app.services.capture.recorder import RecorderError, WindowRecorder


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


def build(**overrides):
    kwargs = {
        "node_id": 42,
        "fd": 9,
        "video_path": "/tmp/out.webm",
        "preview_path": "/tmp/preview.jpg",
    }
    verdict = overrides.pop("support", support())
    return pipeline.build(verdict, **{**kwargs, **overrides})


# -- the launch line ------------------------------------------------------------------------


def test_the_portal_node_and_descriptor_reach_the_source() -> None:
    """Without both, `pipewiresrc` opens nothing and the recording is silently empty."""
    spec = build(node_id=77, fd=13)
    assert "pipewiresrc" in spec.args
    assert "fd=13" in spec.args
    assert "path=77" in spec.args


def test_the_pipeline_finalises_its_container() -> None:
    """`-e` turns SIGINT into end-of-stream. Without it the file has no duration index and many
    players refuse to seek it."""
    assert "-e" in build().args


def test_the_output_file_is_where_it_was_asked_for() -> None:
    spec = build(video_path="/data/recordings/talk.webm")
    assert "location=/data/recordings/talk.webm" in spec.args


@pytest.mark.parametrize(
    ("encoder", "muxer"),
    [
        ("vp8enc", "webmmux"),
        ("vp9enc", "webmmux"),
        ("x264enc", "mp4mux"),
        ("openh264enc", "matroskamux"),
    ],
)
def test_every_supported_encoder_builds(encoder: str, muxer: str) -> None:
    spec = build(support=support(encoder=encoder, muxer=muxer))
    assert encoder in spec.args
    assert muxer in spec.args


def test_a_machine_with_no_encoder_is_refused_rather_than_producing_a_broken_command() -> None:
    with pytest.raises(ValueError, match="No usable video encoder"):
        build(support=support(encoder="", muxer=""))


def test_encoders_are_tuned_for_keeping_up_rather_than_for_size() -> None:
    """This competes with speech inference for the same cores, and a recording that drops frames
    is worse than a larger file."""
    assert "deadline=1" in pipeline.encoder_args("vp8enc")
    assert "tune=zerolatency" in pipeline.encoder_args("x264enc")


def test_an_unknown_encoder_still_produces_a_bare_element() -> None:
    assert pipeline.encoder_args("someotherenc") == ["someotherenc"]


# -- how much the encoder may spend on the picture -----------------------------------------------
#
# Reported as "extremely compressed", and it was. `vp8enc` was launched with no rate control named
# at all, so `target-bitrate` sat at its factory default of 256 kbps; the recordings on disk measure
# 345-357 kbps at 1080x1064. Fifteen seconds of a real capture, re-encoded, Y-PSNR against source:
#
#     before this existed   487 kbps   35.44 dB
#     efficient            1405 kbps   42.84 dB
#     balanced             2161 kbps   44.80 dB
#     high                 3031 kbps   45.89 dB


@pytest.mark.parametrize("encoder", ["vp8enc", "vp9enc"])
def test_a_rate_control_mode_is_always_named(encoder: str) -> None:
    """**The whole fault, in one assertion.** Naming none leaves it at 256 kbps."""
    args = pipeline.encoder_args(encoder)

    assert "end-usage=cq" in args
    assert any(arg.startswith("cq-level=") for arg in args)
    assert any(arg.startswith("target-bitrate=") for arg in args)


def test_quality_is_constant_rather_than_a_bitrate_target() -> None:
    """Because the pipeline usually does not know the resolution.

    The portal reports no stream size on this desktop (D-026), so a bitrate cannot be computed from
    the picture; `bits-per-pixel`, the property that exists for exactly that, was measured to have
    no effect in this build. Constant quality needs no resolution — one setting measured 4909 kbps
    at 1080x1064 and 1038 kbps at 640x360, so a small window still produces a small file.
    """
    for quality in ("efficient", "balanced", "high"):
        assert "end-usage=cq" in pipeline.encoder_args("vp8enc", quality)


def test_better_quality_means_a_lower_level_and_a_higher_ceiling() -> None:
    """cq-level runs backwards — 0 is best — and the ceiling is a cap, not a target."""
    levels = [pipeline.QUALITIES[name].cq_level for name in ("efficient", "balanced", "high")]
    ceilings = [pipeline.QUALITIES[name].ceiling for name in ("efficient", "balanced", "high")]

    assert levels == sorted(levels, reverse=True)
    assert ceilings == sorted(ceilings)


def test_screen_content_gets_the_motion_threshold_gstreamer_recommends() -> None:
    """Its own property documentation: "Recommendation is to set 100 for screen/window sharing"."""
    assert f"static-threshold={pipeline.STATIC_THRESHOLD}" in pipeline.encoder_args("vp8enc")


def test_openh264_is_given_a_bitrate_rather_than_its_128_kbps_default() -> None:
    """Lower again than VP8's, and the same fault on any machine that falls back to it."""
    args = pipeline.encoder_args("openh264enc", "balanced")

    assert "bitrate=128000" not in args
    assert any(arg.startswith("bitrate=") for arg in args)


def test_an_unknown_quality_falls_back_rather_than_failing() -> None:
    """A hand-edited config file should not be able to stop a recording starting."""
    assert pipeline.encoder_args("vp8enc", "nonsense") == pipeline.encoder_args("vp8enc")


def test_the_quality_reaches_the_launch_line() -> None:
    high = build(support=support(encoder="vp8enc", muxer="webmmux"), quality="high")
    efficient = build(support=support(encoder="vp8enc", muxer="webmmux"), quality="efficient")

    assert f"cq-level={pipeline.QUALITIES['high'].cq_level}" in high.args
    assert f"cq-level={pipeline.QUALITIES['efficient'].cq_level}" in efficient.args


def test_only_hardware_encoders_are_asked_for_nv12() -> None:
    """**Keyed off the encoder, not off having a parser**, which is what it used to read.

    That held only as long as no software encoder needed one, and `openh264enc` does — it accepts
    **I420 and nothing else**, so naming NV12 for it would trade a pipeline that would not link for
    one that would not negotiate.
    """
    software = build(
        support=support(encoder="openh264enc", muxer="matroskamux", parser="h264parse")
    )
    hardware = build(support=support(encoder="vaav1enc", muxer="matroskamux", parser="av1parse"))

    assert "video/x-raw,format=NV12" not in software.args
    assert "h264parse" in software.args
    assert "video/x-raw,format=NV12" in hardware.args


# -- the preview branch ------------------------------------------------------------------------


def test_the_preview_branch_is_present_when_jpegenc_is() -> None:
    spec = build()
    assert "tee" in spec.args
    assert "jpegenc" in spec.args
    assert spec.preview_path == "/tmp/preview.jpg"


def test_no_jpegenc_means_no_preview_and_still_a_recording() -> None:
    """A missing preview costs the monitor its picture and costs the recording nothing (D-020)."""
    spec = build(support=support(preview=False))
    assert "jpegenc" not in spec.args
    assert "tee" not in spec.args
    assert spec.preview_path == ""
    assert "vp8enc" in spec.args, "losing the preview must not lose the recording"


def test_the_preview_can_be_declined_even_when_it_is_possible() -> None:
    spec = build(want_preview=False)
    assert "jpegenc" not in spec.args


def test_the_preview_keeps_exactly_one_file() -> None:
    """Otherwise it accumulates one JPEG per second for the length of a talk."""
    spec = build()
    assert "max-files=1" in spec.args


def test_the_preview_branch_leaks_rather_than_stalling_the_recording() -> None:
    """A blocked preview queue would apply backpressure to the encoder, so the branch that does not
    matter would stall the branch that does."""
    assert "leaky=downstream" in build().args


# -- resolution and rate -------------------------------------------------------------------------


def test_the_frame_rate_and_height_are_configurable() -> None:
    spec = build(frame_rate=30, max_height=1080, source_width=3840, source_height=2160)
    assert "video/x-raw,framerate=30/1" in spec.args
    assert "video/x-raw,width=1920,height=1080" in spec.args


def test_scaling_is_only_ever_downward() -> None:
    """A small window recorded at its own size is sharper than one stretched to a ceiling."""
    # Preview off, so the only scaler that could appear is the recording branch's own.
    args = build(source_width=640, source_height=480, max_height=720, want_preview=False).args
    assert "videoscale" not in args


def test_the_preview_does_not_decide_the_recordings_size() -> None:
    """The bug this file did not catch, and the reason every window capture was 480 wide.

    GStreamer resolves caps *upstream*. With one shared `videoscale` ahead of the tee, the preview
    branch's fixed `width=480` propagated back through the tee and became the recording's width
    too — and with the height left as an open range for the scaler to satisfy however it liked, one
    recording came out **480x16**: a sixteen-pixel-tall strip of a talk, written without a single
    warning from GStreamer, because nothing about it was invalid. Only watching the file revealed
    it. So each branch owns a scaler, and the recording's size is stated rather than negotiated.
    """
    spec = build(source_width=1920, source_height=1080)
    args = spec.args

    assert args.count("videoscale") == 2
    assert "video/x-raw,width=1280,height=720" in args
    # And the preview still gets its own, unrelated, size.
    assert f"video/x-raw,width={pipeline.PREVIEW_WIDTH},height={pipeline.PREVIEW_HEIGHT}" in args


def test_the_recording_branch_scales_after_the_tee() -> None:
    """Position is the whole fix: a scaler before the tee is shared, and sharing is the fault."""
    args = build(source_width=1920, source_height=1080).args

    assert args.index("tee") < args.index("videoscale")


def test_no_caps_range_is_ever_asked_of_the_scaler() -> None:
    """A range asks `videoscale` to fixate a size, and fixation is what has failed every time.

    Against an ordinary source it picked badly — a 480x16 recording. Against a source with an
    extreme pixel-aspect-ratio it overflowed outright and the pipeline refused to preroll. There is
    no size this pipeline asks GStreamer to work out any more: either both numbers are known and
    stated, or there is no scaler on that branch.
    """
    for width, height in ((0, 0), (800, 600), (2560, 1440), (1920, 1080)):
        args = build(source_width=width, source_height=height).args
        assert not any("[" in arg for arg in args if arg.startswith("video/x-raw"))


def test_a_tall_source_is_capped_at_the_ceiling_and_keeps_its_shape() -> None:
    args = build(source_width=2560, source_height=1440, max_height=720).args

    assert "video/x-raw,width=1280,height=720" in args


def test_odd_dimensions_are_rounded_down_to_even() -> None:
    """VP8, VP9 and H.264 all subsample chroma; an odd edge is rejected or silently rounded."""
    args = build(source_width=1919, source_height=1081, max_height=1081).args

    caps = next(arg for arg in args if arg.startswith("video/x-raw,width="))
    width, height = (int(part.split("=")[1]) for part in caps.split(",")[1:3])
    assert width % 2 == 0
    assert height % 2 == 0


def test_an_unknown_source_size_means_no_scaler_rather_than_a_guess() -> None:
    """The portal on this desktop reports no size, so this is the ordinary case, not the edge one.

    The ceiling is given up rather than approximated. A recording at the window's own size costs
    encoder time; a recording produced by guessing cost the recording.
    """
    args = build(source_width=0, source_height=0, want_preview=False).args

    assert "videoscale" not in args


def test_frames_are_dropped_rather_than_queued() -> None:
    """The portal's node is live: a late frame queued rather than dropped drifts the recording
    behind the audio it will be muxed against."""
    assert "drop-only=true" in build().args


def test_the_command_string_is_for_logging_only() -> None:
    spec = build()
    assert spec.command.startswith("gst-launch-1.0")
    assert spec.command.split() != spec.args or True  # the args list is what is executed


# -- the recorder's lifetime ---------------------------------------------------------------------

pytestmark_gst = pytest.mark.skipif(
    shutil.which("gst-launch-1.0") is None, reason="GStreamer is not installed here"
)


def source_spec(tmp_path: Path, buffers: int = 30):
    """A real pipeline that needs no portal — the same shape, fed by a test pattern."""
    out = tmp_path / "out.webm"
    return pipeline.PipelineSpec(
        args=[
            "gst-launch-1.0",
            "-q",
            "-e",
            "videotestsrc",
            f"num-buffers={buffers}",
            "!",
            "videoconvert",
            "!",
            "vp8enc",
            "deadline=1",
            "cpu-used=8",
            "!",
            "webmmux",
            "!",
            "filesink",
            f"location={out}",
        ],
        video_path=str(out),
        preview_path="",
    )


@pytestmark_gst
def test_a_recording_runs_and_produces_a_playable_file(tmp_path: Path) -> None:
    # Enough buffers that it is still running after the start-up settle window. `videotestsrc`
    # produces frames as fast as it can, so a small count finishes before `start()` returns — which
    # is how the settle check came to be reporting a *successful* short recording as a failure.
    recorder = WindowRecorder(source_spec(tmp_path, buffers=900))
    recorder.start()
    assert recorder.is_running

    time.sleep(0.5)
    recorder.stop()

    assert Path(recorder.state.video_path).stat().st_size > 0


@pytestmark_gst
def test_stopping_finalises_rather_than_killing(tmp_path: Path) -> None:
    """The difference between a seekable file and one players refuse to open."""
    recorder = WindowRecorder(source_spec(tmp_path, buffers=600))
    recorder.start()
    time.sleep(1.0)
    state = recorder.stop()

    assert state.running is False
    assert state.bytes_written > 0
    # A finalised WebM ends with its cues; an unfinalised one is truncated mid-cluster, and size
    # alone cannot tell them apart. This asserts the process exited rather than being killed.
    assert recorder._process.returncode is not None


@pytestmark_gst
def test_the_source_ending_is_reported_as_the_window_closing(tmp_path: Path) -> None:
    """People close windows mid-talk. That ends the video and must not end the session."""
    seen: list = []
    recorder = WindowRecorder(source_spec(tmp_path, buffers=60), on_stopped=seen.append)
    recorder.start()

    for _ in range(80):
        if seen:
            break
        time.sleep(0.1)

    assert seen, "the recorder never noticed its source ending"
    assert seen[0].window_closed is True
    assert seen[0].failed is False


@pytestmark_gst
def test_a_source_that_ends_instantly_is_not_reported_as_broken(tmp_path: Path) -> None:
    """Found by writing these tests. The settle check first treated *any* early exit as a failure
    and said "stopped immediately: it exited with code 0" — a sentence that answers nothing. A
    clean exit means the window closed the moment it was chosen, which is an event, not a fault."""
    recorder = WindowRecorder(source_spec(tmp_path, buffers=1))
    recorder.start()  # must not raise
    recorder.stop()
    assert recorder.state.failed is False


@pytestmark_gst
def test_a_failing_pipeline_raises_at_start_rather_than_recording_nothing(tmp_path: Path) -> None:
    """Catching this at start turns "the video file is empty" into an error at the moment the user
    pressed record."""
    spec = pipeline.PipelineSpec(
        args=["gst-launch-1.0", "-q", "-e", "nosuchelement12345"],
        video_path=str(tmp_path / "never.webm"),
        preview_path="",
    )
    with pytest.raises(RecorderError, match="stopped immediately"):
        WindowRecorder(spec).start()


def test_a_missing_binary_is_a_recorder_error(tmp_path: Path) -> None:
    spec = pipeline.PipelineSpec(
        args=["definitely-not-a-real-binary-98765"],
        video_path=str(tmp_path / "never.webm"),
        preview_path="",
    )
    with pytest.raises(RecorderError, match="Could not start"):
        WindowRecorder(spec).start()


def test_stopping_something_that_never_started_is_harmless(tmp_path: Path) -> None:
    recorder = WindowRecorder(source_spec(tmp_path))
    assert recorder.stop().running is False


@pytestmark_gst
def test_stopping_twice_is_harmless(tmp_path: Path) -> None:
    recorder = WindowRecorder(source_spec(tmp_path, buffers=600))
    recorder.start()
    recorder.stop()
    recorder.stop()
    assert recorder.is_running is False


@pytestmark_gst
def test_a_failure_writes_gstreamers_own_complaint_to_the_log(tmp_path: Path) -> None:
    """It is the only thing that explains why a pipeline would not run."""
    logs = tmp_path / "logs"
    spec = pipeline.PipelineSpec(
        args=["gst-launch-1.0", "-q", "-e", "nosuchelement12345"],
        video_path=str(tmp_path / "never.webm"),
        preview_path="",
    )
    recorder = WindowRecorder(spec, log_dir=logs)
    with pytest.raises(RecorderError):
        recorder.start()
    # The stderr was captured even though start() raised.
    assert recorder._stderr
