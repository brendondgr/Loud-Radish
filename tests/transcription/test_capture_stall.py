"""A capture that is alive and writing nothing (D-036).

**The failure that had no name.** Measured against a 68-minute seminar recorded on 2026-09-04:
video frames at a steady 15.0 fps from 0.000 s to 882.542 s, then a gap of 882.6 s with no frames
at all, then two frames at 1765.16 s where the pipeline was flushed. For fourteen minutes and
forty-three seconds the recorder had a live process, an open file handle, and nothing whatever to
show for itself — and the supervisor asked only "has it exited", to which the honest answer was no.

So there is a second question, and these tests are what holds it: *is it still writing*. The check
is the output file's size, because that is what recording means from outside the process and
because it needs nothing of the party whose word is in doubt.

The recorder here is driven directly against a real file rather than through GStreamer. What is
under test is the supervisor's arithmetic — when a gap becomes a stall, when it stops being one,
and what the grace window protects — and a real encoder would make each case a matter of timing
rather than of fact.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.services.capture import recorder as recorder_module
from app.services.capture.pipeline import PipelineSpec
from app.services.capture.recorder import WindowRecorder


@pytest.fixture
def sampler(tmp_path: Path, monkeypatch):
    """A recorder wired to a file we grow by hand, with the clock under our control.

    Returns ``(recorder, grow, tick, health)``: `grow` appends bytes to the output file, `tick`
    moves the monotonic clock forward and takes one sample, and `health` collects every state the
    recorder reported a change on.
    """
    video = tmp_path / "video.webm"
    video.write_bytes(b"")
    spec = PipelineSpec(args=["true"], video_path=str(video), preview_path="")

    health: list[tuple[bool, float]] = []
    rec = WindowRecorder(
        spec, on_health=lambda state: health.append((state.stalled, state.stalled_seconds))
    )
    rec.state.running = True

    now = [1_000.0]
    monkeypatch.setattr(recorder_module.time, "monotonic", lambda: now[0])
    rec.state.started_at = now[0]

    def grow(count: int = 4096) -> None:
        with video.open("ab") as handle:
            handle.write(b"\x00" * count)

    def tick(seconds: float) -> None:
        now[0] += seconds
        rec._sample_progress()

    return rec, grow, tick, health


def test_a_capture_that_keeps_writing_is_never_called_stalled(sampler) -> None:
    rec, grow, tick, health = sampler

    for _ in range(60):
        grow()
        tick(2.0)

    assert rec.state.stalled is False
    assert health == []


def test_a_file_that_stops_growing_is_reported_stalled(sampler) -> None:
    """The reported fault, in miniature: writing, then not writing, and still running."""
    rec, grow, tick, health = sampler

    grow()
    tick(1.0)
    assert rec.state.stalled is False

    for _ in range(20):
        tick(1.0)

    assert rec.state.stalled is True
    assert health and health[0][0] is True
    assert health[0][1] >= recorder_module.STALL_TIMEOUT_S


def test_a_gap_shorter_than_the_timeout_is_not_a_stall(sampler) -> None:
    """A motionless slide under a constant-quality encoder writes very little, not nothing."""
    rec, grow, tick, health = sampler

    grow()
    tick(1.0)
    tick(recorder_module.STALL_TIMEOUT_S - 2.0)
    grow()
    tick(1.0)

    assert rec.state.stalled is False
    assert health == []


def test_writing_again_clears_the_stall_and_says_so(sampler) -> None:
    """A banner left standing after a recovery says something untrue about a live recording."""
    rec, grow, tick, health = sampler

    grow()
    tick(1.0)
    for _ in range(20):
        tick(1.0)
    assert rec.state.stalled is True

    grow()
    tick(1.0)

    assert rec.state.stalled is False
    assert [stalled for stalled, _ in health] == [True, False]


def test_the_grace_window_protects_a_pipeline_that_has_not_warmed_up(sampler) -> None:
    """A cold pipeline negotiates caps and warms an encoder before it writes a byte."""
    rec, _grow, tick, health = sampler

    tick(recorder_module.STALL_GRACE_S - 1.0)

    assert rec.state.stalled is False
    assert health == []


def test_a_capture_that_never_writes_at_all_is_stalled_too(sampler) -> None:
    """The loudest case of it, and the one the grace window must not hide for ever."""
    rec, _grow, tick, health = sampler

    tick(recorder_module.STALL_GRACE_S + recorder_module.STALL_TIMEOUT_S + 1.0)

    assert rec.state.stalled is True
    assert health and health[0][0] is True


def test_a_health_callback_that_raises_does_not_kill_the_supervisor(tmp_path, monkeypatch) -> None:
    """The watcher is the only thing that will ever notice the process ending."""
    video = tmp_path / "video.webm"
    video.write_bytes(b"")
    spec = PipelineSpec(args=["true"], video_path=str(video), preview_path="")

    def explode(_state):
        raise RuntimeError("the banner blew up")

    rec = WindowRecorder(spec, on_health=explode)
    rec.state.running = True

    now = [1_000.0]
    monkeypatch.setattr(recorder_module.time, "monotonic", lambda: now[0])
    rec.state.started_at = now[0]

    now[0] += recorder_module.STALL_GRACE_S + recorder_module.STALL_TIMEOUT_S + 1.0
    rec._sample_progress()  # must not raise

    assert rec.state.stalled is True


def test_stalled_seconds_is_zero_when_nothing_is_running(tmp_path) -> None:
    """It is read by the monitor pane on every tick, including between recordings."""
    spec = PipelineSpec(args=["true"], video_path=str(tmp_path / "v.webm"), preview_path="")

    assert WindowRecorder(spec).state.stalled_seconds == 0.0


# -- GStreamer's own account --------------------------------------------------------------------


def test_stderr_goes_to_a_file_rather_than_a_pipe(tmp_path: Path) -> None:
    """An undrained pipe blocks its writer at 64 KB — a way of *causing* the fault above.

    Nothing reads the recorder's stderr until the process has exited, so a pipeline with a great
    deal to say would stop being able to say it and stop encoding at the same moment.
    """
    logs = tmp_path / "logs"
    spec = PipelineSpec(
        args=["sh", "-c", "echo 'a pipeline complaint' >&2; exit 3"],
        video_path=str(tmp_path / "never.webm"),
        preview_path="",
    )
    rec = WindowRecorder(spec, log_dir=logs)

    with pytest.raises(recorder_module.RecorderError):
        rec.start()

    assert (logs / "capture.log").read_text().strip() == "a pipeline complaint"
    assert any("a pipeline complaint" in chunk for chunk in rec._stderr)


def test_each_segment_can_keep_its_own_account(tmp_path: Path) -> None:
    """A resumed capture must not overwrite the log that says why it had to resume."""
    logs = tmp_path / "logs"
    for name, text in (("capture.log", "first"), ("capture-002.log", "second")):
        spec = PipelineSpec(
            args=["sh", "-c", f"echo '{text}' >&2; exit 3"],
            video_path=str(tmp_path / "never.webm"),
            preview_path="",
        )
        with pytest.raises(recorder_module.RecorderError):
            WindowRecorder(spec, log_dir=logs, log_name=name).start()

    assert (logs / "capture.log").read_text().strip() == "first"
    assert (logs / "capture-002.log").read_text().strip() == "second"


def test_the_pipeline_is_no_longer_quiet() -> None:
    """`-q` suppressed the bus messages that would have named the cause of the stall."""
    from app.services.capture import pipeline as pipeline_module

    spec = pipeline_module.build(
        pipeline_module.CaptureSupport(
            available=True,
            session_type="wayland",
            portal_version=5,
            encoder="vp8enc",
            muxer="webmmux",
            extension="webm",
            preview=False,
        ),
        node_id=7,
        fd=3,
        video_path="/tmp/x.webm",
        preview_path="",
    )

    assert "-q" not in spec.args
