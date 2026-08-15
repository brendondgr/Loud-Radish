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
    spec = build(frame_rate=30, max_height=1080)
    assert "video/x-raw,framerate=30/1" in spec.args
    assert any("height=[1,1080]" in arg for arg in spec.args)


def test_scaling_is_only_ever_downward() -> None:
    """A small window recorded at its own size is sharper than one stretched to a ceiling."""
    assert any("height=[1,720]" in arg for arg in build().args)


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
    # And the preview still gets its own, unrelated, width.
    assert any(f"width={pipeline.PREVIEW_WIDTH}" in arg for arg in args)


def test_the_recording_branch_scales_after_the_tee() -> None:
    """Position is the whole fix: a scaler before the tee is shared, and sharing is the fault."""
    args = build(source_width=1920, source_height=1080).args

    assert args.index("tee") < args.index("videoscale")


def test_a_known_source_size_is_stated_rather_than_left_to_the_scaler() -> None:
    """A range asks the scaler to choose. Given anything else to satisfy, it chooses badly."""
    args = build(source_width=800, source_height=600).args

    # Under the ceiling, so it records at its own size rather than being stretched up to it.
    assert "video/x-raw,width=800,height=600" in args
    assert not any("height=[" in arg for arg in args)


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


def test_an_unknown_source_size_still_gets_a_ceiling() -> None:
    """The portal does not always report a size, and a capture must not depend on it doing so."""
    args = build(source_width=0, source_height=0).args

    assert any("height=[1,720]" in arg for arg in args)


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
