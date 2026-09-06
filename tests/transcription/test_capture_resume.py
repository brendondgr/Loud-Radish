"""A capture that dies mid-session reopens the portal and carries on (D-036).

The reported fault, once: a 68-minute Zoom seminar whose video stopped at 14 m 43 s, whose recorder
process finally ended at 29 m 25 s, and whose remaining thirty-nine minutes were never offered to
an encoder — because `_start_window_capture` is called exactly once per session and
`_on_recorder_stopped` emitted a banner and returned.

Three properties live here.

**A capture that ends while the session is running is reopened.** "The window was closed" and "the
stream went away" are indistinguishable from inside this process — a video call that drops its
connection and rebuilds its surface produces exactly the clean end that a person closing a window
does — so both are treated as resumable, and the banner is what happens when reopening fails.

**Reopening is bounded and silent.** Five attempts, no two within the backoff, and never without a
stored consent token — because the compositor's answer to a token that will not restore is to put
its picker on screen, over the talk being recorded.

**The pieces go back onto one timeline with the gaps held.** A gap dropped rather than held would
shorten the video by exactly the length of the failure, sliding every frame after it out of sync
with every timestamp in every export.

The portal and the recorder are stubbed, as everywhere else in this area: the real ones put a dialog
on screen and wait for a human.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from app.config import ConfigStore
from app.models.session import SessionMetadata
from app.services.capture import stitch as stitch_module
from app.services.capture.stitch import CaptureSegment, stitch
from app.services.session import CaptureOptions, SessionManager, modes
from app.services.session import manager as manager_module

from .capture_doubles import (
    Credentials,
    FakePortal,
    FakeRecorder,
    Recorder,
    install,
    write_talk_wav,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def stub_capture(monkeypatch):
    install(monkeypatch)
    yield


@pytest.fixture
def manager(tmp_path: Path):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav")),
            "audio.file_speed": 0.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "recording.recording_dir": str(tmp_path / "recordings"),
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
        }
    )
    events = Recorder()
    return SessionManager(store, emit=events, session_dir=tmp_path / "sessions"), store, events


def window() -> SessionMetadata:
    return SessionMetadata(session_id=uuid.uuid4().hex[:12], mode=modes.WINDOW)


def end_recorder(recorder, *, window_closed: bool = True) -> None:
    """The stream went away, from the recorder's point of view."""
    recorder.state.window_closed = window_closed
    recorder.state.failed = not window_closed
    recorder.state.running = False
    recorder.state.stopped_at = time.monotonic()
    recorder.on_stopped(recorder.state)


# -- reopening ----------------------------------------------------------------------------------


async def test_a_capture_that_ends_mid_session_is_reopened(manager) -> None:
    session, _store, events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        end_recorder(FakeRecorder.instances[0])

        assert len(FakeRecorder.instances) == 2, (
            "the rest of the talk was never offered to an encoder"
        )
        assert FakeRecorder.instances[1].started is True
        assert "capture-window-closed" not in events.codes()
        assert session.is_running is True
    finally:
        await session.stop()


async def test_the_second_piece_gets_its_own_file(manager) -> None:
    """Appending to a finalised container is not a thing that can be done safely."""
    session, _store, _events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        end_recorder(FakeRecorder.instances[0])

        first = Path(FakeRecorder.instances[0].spec.video_path)
        second = Path(FakeRecorder.instances[1].spec.video_path)
        assert first.name == "video.webm"
        assert second.name == "video.002.webm"
        assert first.parent == second.parent
    finally:
        await session.stop()


async def test_a_recorder_that_failed_is_reopened_too(manager) -> None:
    """A non-zero exit and a clean one are the same event from here: the pictures stopped."""
    session, _store, _events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        end_recorder(FakeRecorder.instances[0], window_closed=False)

        assert len(FakeRecorder.instances) == 2
    finally:
        await session.stop()


async def test_without_a_stored_consent_it_gives_up_rather_than_prompting(manager) -> None:
    """A picker thrown across a live seminar is worse than a video that ended.

    Consent is dropped after the session has started, which is the shape of the real case: a token
    is single-use, the desktop can refuse to restore it, and there is then nothing to reopen with.
    """
    session, _store, events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        session.credentials.values.clear()
        end_recorder(FakeRecorder.instances[0])

        assert len(FakeRecorder.instances) == 1
        assert len(FakePortal.instances) == 1, "the compositor was asked again"
        assert "capture-window-closed" in events.codes()
    finally:
        await session.stop()


async def test_a_portal_that_refuses_to_reopen_ends_the_video_and_says_so(manager) -> None:
    """The banner is what happens when reopening fails, not what happens instead of trying."""
    session, _store, events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        FakePortal.behaviour = "decline"
        end_recorder(FakeRecorder.instances[0])

        assert len(FakeRecorder.instances) == 1
        assert "capture-window-closed" in events.codes()
        assert session.is_running is True, "a video that cannot resume must not end the talk"
    finally:
        FakePortal.behaviour = "grant"
        await session.stop()


async def test_reopening_is_bounded(manager, monkeypatch) -> None:
    """A portal that has failed five times is not coming back, and a loop that keeps asking it
    fills the recording folder with empty segments."""
    monkeypatch.setattr(manager_module, "RESUME_BACKOFF_S", 0.0)
    session, _store, _events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        for _ in range(manager_module.MAX_CAPTURE_RESUMES + 3):
            end_recorder(FakeRecorder.instances[-1])

        assert len(FakeRecorder.instances) == manager_module.MAX_CAPTURE_RESUMES + 1
    finally:
        await session.stop()


async def test_two_attempts_inside_the_backoff_count_as_one(manager) -> None:
    """A capture that dies the instant it starts would otherwise burn the whole allowance."""
    session, _store, _events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        end_recorder(FakeRecorder.instances[0])
        end_recorder(FakeRecorder.instances[1])

        assert len(FakeRecorder.instances) == 2, "the backoff was not applied"
    finally:
        await session.stop()


async def test_a_deliberate_stop_is_never_a_resume(manager) -> None:
    """Pressing stop must not reopen the portal, which would ask for consent after the fact."""
    session, _store, _events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    await session.stop()

    assert len(FakeRecorder.instances) == 1
    assert FakePortal.instances[0].closed is True


async def test_a_stall_ends_the_capture_and_opens_its_successor(manager) -> None:
    """Waiting for a stalled pipeline to end on its own cost this recording fifteen minutes."""
    session, _store, events = manager
    session.credentials = Credentials()
    await session.start(window(), options=CaptureOptions())
    try:
        first = FakeRecorder.instances[0]
        first.state.stalled = True
        first.state.last_growth_at = time.monotonic() - 30.0
        session._on_recorder_health(first.state)

        for _ in range(200):
            if len(FakeRecorder.instances) > 1:
                break
            await asyncio.sleep(0.01)

        assert len(FakeRecorder.instances) == 2, "the stall was reported and then left alone"
        assert first.stopped is True
        assert "capture-stalled" in events.codes()
    finally:
        await session.stop()


# -- putting the pieces back together -----------------------------------------------------------


def encode(path: Path, seconds: float, *, size: str = "160x120", rate: int = 15) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={rate}:duration={seconds}",
            "-c:v",
            "libvpx",
            "-b:v",
            "120k",
            str(path),
        ],
        check=True,
    )
    return path


needs_ffmpeg = pytest.mark.skipif(
    not stitch_module.available(), reason="ffmpeg is needed to join real files"
)


@needs_ffmpeg
def test_the_gap_between_two_pieces_is_held_rather_than_dropped(tmp_path: Path) -> None:
    """Dropped, the video would be exactly as much shorter as the recording had failed for — and
    every frame after the gap would sit that far ahead of its own transcript line."""
    first = encode(tmp_path / "video.webm", 3.0)
    second = encode(tmp_path / "video.002.webm", 3.0)

    result = stitch(
        [CaptureSegment(first, 0.0), CaptureSegment(second, 10.0)], tmp_path / "joined.webm"
    )

    assert result.ok, result.reason
    assert result.filled_s == pytest.approx(7.0, abs=0.3)
    assert _duration(tmp_path / "joined.webm") == pytest.approx(13.0, abs=0.5)


@needs_ffmpeg
def test_the_joined_file_keeps_the_source_codec_and_geometry(tmp_path: Path) -> None:
    """The join is a stream copy, which is only possible while every piece matches."""
    first = encode(tmp_path / "video.webm", 2.0, size="320x240")
    second = encode(tmp_path / "video.002.webm", 2.0, size="320x240")

    result = stitch(
        [CaptureSegment(first, 0.0), CaptureSegment(second, 5.0)], tmp_path / "joined.webm"
    )

    assert result.ok, result.reason
    assert _shape(tmp_path / "joined.webm") == ("vp8", "320", "240")


@needs_ffmpeg
def test_pieces_are_ordered_by_when_they_were_captured(tmp_path: Path) -> None:
    first = encode(tmp_path / "video.webm", 2.0)
    second = encode(tmp_path / "video.002.webm", 2.0)

    result = stitch(
        [CaptureSegment(second, 8.0), CaptureSegment(first, 0.0)], tmp_path / "joined.webm"
    )

    assert result.ok, result.reason
    assert result.filled_s == pytest.approx(6.0, abs=0.3)


@needs_ffmpeg
def test_a_gap_too_small_to_matter_gets_no_filler(tmp_path: Path) -> None:
    first = encode(tmp_path / "video.webm", 2.0)
    second = encode(tmp_path / "video.002.webm", 2.0)

    result = stitch(
        [CaptureSegment(first, 0.0), CaptureSegment(second, 2.05)], tmp_path / "joined.webm"
    )

    assert result.ok, result.reason
    assert result.filled_s == 0.0


@needs_ffmpeg
def test_an_impossible_gap_keeps_the_pieces_apart(tmp_path: Path) -> None:
    """Filling one would write hours of held frame off a number that has gone wrong."""
    first = encode(tmp_path / "video.webm", 2.0)
    second = encode(tmp_path / "video.002.webm", 2.0)
    output = tmp_path / "joined.webm"

    result = stitch([CaptureSegment(first, 0.0), CaptureSegment(second, 10 * 3600.0)], output)

    assert result.ok is False
    assert "fault rather than a measurement" in result.reason
    assert first.exists() and second.exists()
    assert not output.exists()


def test_one_piece_is_left_exactly_as_it_is(tmp_path: Path) -> None:
    """A capture that never broke must not be re-containered for the sake of uniformity."""
    only = tmp_path / "video.webm"
    only.write_bytes(b"not really a video, and never opened")

    result = stitch([CaptureSegment(only, 0.0)], tmp_path / "joined.webm")

    assert result.ok is True
    assert result.path == str(only)
    assert not (tmp_path / "joined.webm").exists()


def test_no_pieces_is_a_refusal_rather_than_an_empty_file(tmp_path: Path) -> None:
    result = stitch([], tmp_path / "joined.webm")

    assert result.ok is False
    assert not (tmp_path / "joined.webm").exists()


@needs_ffmpeg
def test_an_unreadable_piece_keeps_every_piece(tmp_path: Path) -> None:
    """Combining them wrongly would be worse than not combining them."""
    first = encode(tmp_path / "video.webm", 2.0)
    junk = tmp_path / "video.002.webm"
    junk.write_bytes(b"truncated mid-cluster")

    result = stitch([CaptureSegment(first, 0.0), CaptureSegment(junk, 5.0)], tmp_path / "j.webm")

    assert result.ok is False
    assert first.exists() and junk.exists()


@needs_ffmpeg
def test_nothing_is_left_behind_in_the_recording_folder(tmp_path: Path) -> None:
    """A recording folder is a user directory, and scratch files in one are litter."""
    first = encode(tmp_path / "video.webm", 2.0)
    second = encode(tmp_path / "video.002.webm", 2.0)

    stitch([CaptureSegment(first, 0.0), CaptureSegment(second, 6.0)], tmp_path / "joined.webm")

    assert not (tmp_path / ".stitch").exists()


# -- the manager's own arithmetic ---------------------------------------------------------------


@needs_ffmpeg
async def test_the_session_joins_its_pieces_under_the_plain_name(manager) -> None:
    """Everything downstream reads one `video.webm`. Segments must not become its problem.

    The start of each piece is derived the way the mux's offset always has been — the moment it was
    told to stop, minus its own encoded duration — because nothing observes the near end of a
    GStreamer pipeline. This checks that derivation against files whose real lengths are known.
    """
    session, _store, _events = manager
    directory = Path(session._config.resolve().recording.recording_dir) / "joined-by-hand"
    directory.mkdir(parents=True, exist_ok=True)
    first = encode(directory / "video.webm", 3.0)
    second = encode(directory / "video.002.webm", 3.0)

    # Piece one ran 0–3 s, piece two 10–13 s, on the recorder's monotonic clock.
    session._capture_segments = [(str(first), 1_000.0 + 3.0), (str(second), 1_000.0 + 13.0)]
    session._join_capture_segments()

    assert session._video_path == str(first)
    assert not second.exists(), "the pieces were left beside the file that replaced them"
    assert _duration(first) == pytest.approx(13.0, abs=0.5)


@needs_ffmpeg
async def test_a_single_piece_is_not_rewritten(manager) -> None:
    session, _store, _events = manager
    directory = Path(session._config.resolve().recording.recording_dir) / "one-piece"
    directory.mkdir(parents=True, exist_ok=True)
    only = encode(directory / "video.webm", 2.0)
    before = only.read_bytes()

    session._capture_segments = [(str(only), 1_000.0 + 2.0)]
    session._join_capture_segments()

    assert session._video_path == str(only)
    assert only.read_bytes() == before


async def test_no_pieces_leaves_nothing_for_the_mux_to_find(manager) -> None:
    session, _store, _events = manager
    session._capture_segments = []
    session._video_path = "stale"

    session._join_capture_segments()

    assert session._video_path == ""


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _shape(path: Path) -> tuple[str, str, str]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    values = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return values[0], values[1], values[2]
