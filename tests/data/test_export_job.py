"""The export as a staged job with options, estimates, and progress (D-037).

Reported together: "I have no control over output quality or file size… a roughly 30-minute
recording came out at about 100 MB", and "press export and watch the pipeline run through its
stages… instead of a black box."

Three things are held here.

**A plan is resolved against the recording, not against a setting.** The reported seminar asked for
`capture.max_height: 720` and recorded at 2560 x 1532, because the capture scaler is omitted
whenever the portal reports no geometry. A window offering "720p" against the configured number
would be describing a file that does not exist.

**Ceilings are ceilings.** A window already recorded at 640 x 360 is left alone rather than scaled
up to meet a preset, which would spend minutes making a larger file that shows less.

**The bar is weighted by predicted cost.** Four stages of which one takes minutes and three take a
second would otherwise sit at 25 % for the whole encode and then jump, which is the specific way a
progress bar stops being believed.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.export import PRESETS, by_id, estimate, probe
from app.services.export.encode import build_command
from app.services.export.job import ExportJob, ExportRegistry, ExportStage, StageState
from app.services.export.presets import BALANCED, ORIGINAL
from app.services.export.profile import SourceProfile
from app.services.transcript.store import TranscriptStore
from fastapi.testclient import TestClient

KEY = "20260904-155600-08ab28c2d732"

needs_ffmpeg = pytest.mark.skipif(
    not build_command  # always truthy; the real gate is below
    or subprocess.run(["which", "ffmpeg"], capture_output=True, check=False).returncode != 0,
    reason="ffmpeg is needed to encode a real file",
)


def encode_sample(path: Path, *, seconds: float = 2.0, size: str = "640x360") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=15:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libvpx", "-b:v", "300k", "-c:a", "libopus", "-b:a", "64k",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


@pytest.fixture
def dirs(tmp_path):
    sessions = tmp_path / "sessions"
    recordings = tmp_path / "recordings"
    sessions.mkdir()
    recordings.mkdir()
    return sessions, recordings


@pytest.fixture
def config(tmp_path, dirs):
    sessions, recordings = dirs
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {"storage.session_dir": str(sessions), "recording.recording_dir": str(recordings)}
    )
    return store


@pytest.fixture
def client(config):
    app = create_app(config=config)
    with TestClient(app) as client:
        yield client


def write_session(sessions: Path, segments: int = 3) -> Path:
    path = sessions / f"{KEY}.db"
    metadata = SessionMetadata(session_id=KEY.split("-")[-1], title="Operator theory")
    with TranscriptStore(path, metadata=metadata) as store:
        for index in range(segments):
            store.append_segment(
                Segment(id=index + 1, text=f"line {index}", start=index * 5.0, end=index * 5.0 + 4.0)
            )
    return path


# -- what a plan resolves to ---------------------------------------------------------------------


def test_a_plan_resolves_against_the_recording_and_not_against_a_setting() -> None:
    """The reported seminar asked for 720p at capture and recorded at 2560x1532."""
    source = SourceProfile(
        path="/x.webm", width=2560, height=1532, frame_rate=15.0, duration_s=1765.0,
        size_bytes=129_242_669, video_codec="vp8", audio_codec="opus",
    )  # fmt: skip

    assert BALANCED.resolved_height(source) == 720
    # Even, and in the source's own aspect ratio rather than a nominal 16:9.
    assert BALANCED.resolved_width(source) == 1204
    assert BALANCED.resolved_width(source) % 2 == 0


def test_a_ceiling_never_scales_a_small_recording_up() -> None:
    """Spending minutes to make a larger file that shows less is not an improvement."""
    source = SourceProfile(
        path="/x.webm", width=640, height=360, frame_rate=10.0, duration_s=60.0,
        size_bytes=1_000_000, video_codec="vp8", audio_codec="opus",
    )  # fmt: skip

    assert BALANCED.resolved_height(source) == 360
    assert BALANCED.resolved_width(source) == 640
    assert BALANCED.resolved_frame_rate(source) == 10.0


def test_the_command_only_scales_when_scaling_changes_something() -> None:
    small = SourceProfile(
        path="/x.webm", width=640, height=360, frame_rate=10.0, duration_s=60.0,
        size_bytes=1_000_000, video_codec="vp8", audio_codec="opus",
    )  # fmt: skip
    big = SourceProfile(
        path="/x.webm", width=2560, height=1532, frame_rate=30.0, duration_s=60.0,
        size_bytes=9_000_000, video_codec="vp8", audio_codec="opus",
    )  # fmt: skip

    assert "-vf" not in build_command(BALANCED, small, Path("/tmp/o.mp4"))
    assert "scale=-2:720,fps=15" in build_command(BALANCED, big, Path("/tmp/o.mp4"))


def test_a_copy_is_a_copy(tmp_path) -> None:
    source = SourceProfile(path="/x.webm", width=640, height=360, frame_rate=10.0, duration_s=60.0,
                           size_bytes=1_000_000, video_codec="vp8", audio_codec="opus")  # fmt: skip

    command = build_command(ORIGINAL, source, tmp_path / "out.webm")

    assert command[-3:-1] == ["-c", "copy"]
    assert "libx264" not in command


def test_a_silent_recording_is_not_given_an_audio_track() -> None:
    source = SourceProfile(path="/x.webm", width=640, height=360, frame_rate=10.0, duration_s=60.0,
                           size_bytes=1_000_000, video_codec="vp8")  # fmt: skip

    assert "-an" in build_command(BALANCED, source, Path("/tmp/o.mp4"))


# -- what it will cost ---------------------------------------------------------------------------


def test_an_estimate_shrinks_with_the_picture() -> None:
    source = SourceProfile(
        path="/x.webm", width=2560, height=1532, frame_rate=15.0, duration_s=1765.0,
        size_bytes=129_242_669, video_codec="vp8", audio_codec="opus",
    )  # fmt: skip

    sizes = [estimate(by_id(name), source).bytes for name in ("high", "balanced", "small")]

    assert sizes == sorted(sizes, reverse=True), sizes
    assert sizes[1] < source.size_bytes / 2, "the whole complaint was that this is too large"


def test_an_estimate_is_a_range_and_says_which_one_is_not() -> None:
    source = SourceProfile(
        path="/x.webm", width=1280, height=720, frame_rate=15.0, duration_s=600.0,
        size_bytes=50_000_000, video_codec="vp8", audio_codec="opus",
    )  # fmt: skip

    guess = estimate(BALANCED, source)
    known = estimate(ORIGINAL, source)

    assert guess.low_bytes < guess.bytes < guess.high_bytes
    assert guess.exact is False
    # A copy is arithmetic, not a prediction: the bytes are on disk.
    assert known.exact is True
    assert known.bytes == known.low_bytes == known.high_bytes == source.size_bytes


def test_an_unreadable_recording_estimates_nothing_and_explains() -> None:
    broken = SourceProfile(path="/x.webm", problem="Not a media file this machine can read.")

    result = estimate(BALANCED, broken)

    assert result.bytes == 0
    assert "media file" in result.problem


def test_an_audio_only_export_carries_almost_no_uncertainty() -> None:
    """It is a requested bitrate, not a prediction about content nobody has watched."""
    source = SourceProfile(
        path="/x.webm", width=1280, height=720, frame_rate=15.0, duration_s=600.0,
        size_bytes=50_000_000, video_codec="vp8", audio_codec="opus",
    )  # fmt: skip

    result = estimate(by_id("audio"), source)

    assert result.high_bytes / result.low_bytes < 1.15


# -- the job -------------------------------------------------------------------------------------


def job_of(*weights: float) -> ExportJob:
    return ExportJob(
        id="j",
        key=KEY,
        preset_id="balanced",
        stages=[
            ExportStage(id=f"s{index}", label=f"Stage {index}", weight=weight, predicted_s=weight)
            for index, weight in enumerate(weights)
        ],
    )


def test_the_bar_is_weighted_by_cost_rather_than_by_stage_count() -> None:
    """Three instant stages and one long one, all at 25 %, is how a bar stops being believed."""
    job = job_of(0.01, 0.96, 0.01, 0.02)

    job.complete("s0")

    assert job.progress == pytest.approx(0.01, abs=0.005)


def test_a_stage_never_walks_backward() -> None:
    job = job_of(1.0)
    job.advance("s0", 0.7)
    job.advance("s0", 0.3)

    assert job.stage("s0").progress == pytest.approx(0.7)


def test_a_skipped_stage_says_skipped_rather_than_flashing_to_done() -> None:
    job = job_of(0.5, 0.5)
    job.skip("s1", "Copied as recorded")

    assert job.stage("s1").state is StageState.SKIPPED
    assert job.progress == pytest.approx(0.5)


def test_a_failure_names_the_stage_it_happened_in() -> None:
    job = job_of(0.5, 0.5)
    job.fail("the encoder fell over", stage_id="s1")

    assert job.stage("s1").state is StageState.FAILED
    assert job.stage("s1").detail == "the encoder fell over"
    assert job.as_event()["state"] == "failed"


def test_the_remaining_time_comes_from_what_a_stage_has_actually_done() -> None:
    """A prediction is what you have before the work starts; a rate is better once it has."""
    job = job_of(1.0)
    job.begin("s0")
    job.stages[0].started_at -= 20.0
    job.advance("s0", 0.25)

    # Twenty seconds bought a quarter, so three quarters is about sixty more.
    assert job.stage("s0").remaining_s == pytest.approx(60.0, rel=0.2)


def test_only_one_export_runs_at_a_time() -> None:
    """They use every core the machine has, which is also the transcriber's."""
    registry = ExportRegistry()

    assert registry.claim(job_of(1.0)) is True
    assert registry.claim(job_of(1.0)) is False


def test_a_finished_export_is_still_findable() -> None:
    """The file has to be collected after the job ends, possibly after a reload."""
    registry = ExportRegistry()
    job = job_of(1.0)
    registry.claim(job)
    job.finish("/tmp/x.zip", 1234)

    assert registry.current() is job
    assert registry.claim(job_of(1.0)) is True, "a finished job must not block the next one"


# -- end to end ----------------------------------------------------------------------------------


@needs_ffmpeg
def test_an_export_runs_its_stages_and_produces_an_archive(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    encode_sample(recordings / KEY / "video-with-audio.webm")

    started = client.post(f"/api/sessions/{KEY}/export/start?preset=small")
    assert started.status_code == 200, started.text

    for _ in range(600):
        body = client.get(f"/api/sessions/{KEY}/export/status").json()["job"]
        if body["state"] != "running":
            break
    assert body["state"] == "done", body.get("error")
    assert [stage["state"] for stage in body["stages"]] == ["done", "done", "done", "done"]
    assert body["progress"] == pytest.approx(1.0)

    archive = client.get(f"/api/sessions/{KEY}/export/result")
    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"
    assert len(archive.content) > 0


@needs_ffmpeg
def test_the_original_preset_skips_the_encode_entirely(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    encode_sample(recordings / KEY / "video-with-audio.webm")

    client.post(f"/api/sessions/{KEY}/export/start?preset=original")
    for _ in range(600):
        body = client.get(f"/api/sessions/{KEY}/export/status").json()["job"]
        if body["state"] != "running":
            break

    assert body["state"] == "done", body.get("error")
    assert body["stages"][1]["state"] == "skipped"


@needs_ffmpeg
def test_the_options_endpoint_measures_the_recording(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    encode_sample(recordings / KEY / "video-with-audio.webm", size="1280x720")

    body = client.get(f"/api/sessions/{KEY}/export/options").json()

    assert body["source"]["width"] == 1280
    assert body["source"]["height"] == 720
    assert [preset["id"] for preset in body["presets"]] == [plan.id for plan in PRESETS]
    assert body["default"] == "balanced"
    # Every preset carries what it would produce for *this* file, not a nominal number.
    balanced = next(p for p in body["presets"] if p["id"] == "balanced")
    assert balanced["output"]["height"] == 720
    assert balanced["estimate"]["low_bytes"] < balanced["estimate"]["high_bytes"]


def test_an_unknown_preset_is_refused_by_name(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    (recordings / KEY).mkdir(parents=True)
    (recordings / KEY / "video-with-audio.webm").write_bytes(b"x" * 100)

    response = client.post(f"/api/sessions/{KEY}/export/start?preset=enormous")

    assert response.status_code == 422
    assert "balanced" in response.json()["detail"]["error"]["message"]


def test_a_recording_with_no_video_is_refused_rather_than_half_exported(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    (recordings / KEY).mkdir(parents=True)

    response = client.post(f"/api/sessions/{KEY}/export/start")

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "not-exportable"


def test_asking_for_a_result_before_there_is_one_says_so(client, dirs) -> None:
    sessions, _recordings = dirs
    write_session(sessions)

    response = client.get(f"/api/sessions/{KEY}/export/result")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "no-export"


def test_a_finished_job_stops_counting() -> None:
    """It reported "took 14 s" to the window that watched it and "took 1 min" a minute later.

    The same fault the session clock had (D-033), found in the browser against a real export: the
    elapsed figure was derived from `now`, so every reader got a different answer about a file that
    had stopped changing.
    """
    job = job_of(1.0)
    job.finish("/tmp/x.zip", 1234)
    settled = job.elapsed_s

    time.sleep(0.05)

    assert job.elapsed_s == pytest.approx(settled)


def test_a_failed_job_stops_counting_too() -> None:
    job = job_of(1.0)
    job.fail("the encoder fell over")
    settled = job.elapsed_s

    time.sleep(0.05)

    assert job.elapsed_s == pytest.approx(settled)
