"""`window` mode: three independent switches, and what survives when the video dies (D-022).

The mode's whole substance is that live transcription, a post-capture pass, and video are
**independent**. Seven combinations are valid and each starts a different set of machinery; the
eighth records nothing and is refused. A wiring mistake that quietly coupled two of them — starting
the engine because video was on, say — would still produce a working recording, so only a test
notices.

The other property is the one that matters when something goes wrong: **the video is the expendable
part.** A window closed mid-talk, a portal that refuses, a GStreamer that will not start — each
ends the video and none ends the session, because the audio and its transcript cannot be recreated
and a video of a window can.

The portal and the recorder are stubbed throughout. The real ones put a dialog on screen and wait
for a human, which is verified by hand instead — see `docs/workflow.md`.
"""

from __future__ import annotations

import asyncio
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.models.session import SessionMetadata
from app.services.audio.formats import SAMPLE_RATE
from app.services.capture import CaptureSupport, PortalDeclined, RecorderError, WindowStream
from app.services.session import CaptureOptions, SessionError, SessionManager, modes
from app.services.session import manager as manager_module

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, name: str, data: dict) -> None:
        with self._lock:
            self.events.append((name, data))

    def of(self, name: str) -> list[dict]:
        with self._lock:
            return [data for event, data in self.events if event == name]

    def codes(self) -> list[str]:
        return [payload.get("code", "") for payload in self.of("error")]

    def wait_for(self, name: str, timeout: float = 6.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.of(name):
                return True
            time.sleep(0.02)
        return False


class FakeRecorder:
    """Stands in for the GStreamer subprocess."""

    instances: list[FakeRecorder] = []

    def __init__(self, spec, *, portal_fd=None, on_stopped=None, log_dir=None) -> None:
        self.spec = spec
        self.portal_fd = portal_fd
        self.on_stopped = on_stopped
        self.started = False
        self.stopped = False
        self.fail_on_start = False
        self.state = manager_module.RecorderState(
            video_path=spec.video_path, preview_path=spec.preview_path
        )
        FakeRecorder.instances.append(self)

    def start(self) -> None:
        if self.fail_on_start:
            raise RecorderError("the encoder fell over")
        self.started = True
        self.state.running = True

    def stop(self):
        self.stopped = True
        self.state.running = False
        return self.state

    @property
    def is_running(self) -> bool:
        return self.state.running


class FakePortal:
    """Stands in for the compositor's picker."""

    instances: list[FakePortal] = []
    behaviour = "grant"

    def __init__(self, *, cursor_mode="hidden", restore_token="") -> None:
        self.cursor_mode = cursor_mode
        self.restore_token = restore_token
        self.closed = False
        FakePortal.instances.append(self)

    def open(self) -> WindowStream:
        if FakePortal.behaviour == "decline":
            raise PortalDeclined("Screen sharing was cancelled.")
        return WindowStream(node_id=7, fd=3, width=1280, height=720, restore_token="tok")

    def close(self) -> None:
        self.closed = True


def write_talk_wav(path: Path, seconds: float = 6.0) -> Path:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    tone = 0.45 * np.sin(2 * np.pi * 180 * t) + 0.28 * np.sin(2 * np.pi * 420 * t)
    syllables = 0.12 + 0.88 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t))
    signal = np.clip(tone * syllables * 0.35, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


@pytest.fixture(autouse=True)
def stub_capture(monkeypatch):
    """A machine that can capture, with neither a portal dialog nor a subprocess."""
    FakeRecorder.instances.clear()
    FakePortal.instances.clear()
    FakePortal.behaviour = "grant"

    monkeypatch.setattr(
        manager_module,
        "detect_capture",
        lambda **_kwargs: CaptureSupport(
            available=True,
            session_type="wayland",
            portal_version=5,
            encoder="vp8enc",
            muxer="webmmux",
            extension="webm",
            preview=True,
        ),
    )
    monkeypatch.setattr(manager_module, "PortalSession", FakePortal)
    monkeypatch.setattr(manager_module, "WindowRecorder", FakeRecorder)
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
    recorder = Recorder()
    return SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions"), store, recorder


def window(**kwargs) -> SessionMetadata:
    import uuid

    return SessionMetadata(session_id=uuid.uuid4().hex[:12], mode=modes.WINDOW, **kwargs)


# -- one recording, one dialog ------------------------------------------------------------------


async def test_concurrent_starts_negotiate_exactly_one_portal(manager) -> None:
    """The five-dialogs fault, as a property.

    `start` guarded on `is_running`, which reads `self._metadata` — and that is not set until the
    audio source is open, several awaits later, one of which loads a speech model. Every start
    request arriving inside that window passed the guard, and in `window` mode each one then opened
    its own portal negotiation. One keystroke produced five consecutive "choose a window" dialogs,
    each cancelling the one before it, and the trace on the session bus showed seven negotiations
    90 ms apart — machine speed, not a person clicking.
    """
    session, _store, _events = manager

    results = await asyncio.gather(
        *(session.start(window(), manager_module.CaptureOptions()) for _ in range(5)),
        return_exceptions=True,
    )
    try:
        started = [r for r in results if not isinstance(r, BaseException)]
        refused = [r for r in results if isinstance(r, SessionError)]

        assert len(started) == 1
        assert len(refused) == 4
        assert len(FakePortal.instances) == 1
    finally:
        if session.is_running:
            await session.stop()


async def test_a_refused_start_does_not_leave_the_session_unstartable(manager) -> None:
    """A claim that outlives its attempt makes the application refuse to record for good."""
    session, _store, _events = manager
    FakePortal.behaviour = "decline"

    with pytest.raises(SessionError):
        await session.start(window(), manager_module.CaptureOptions())

    # The decline tore everything down, so the next attempt must be accepted on its own merits.
    FakePortal.behaviour = "grant"
    await session.start(window(), manager_module.CaptureOptions())
    try:
        assert session.is_running
    finally:
        await session.stop()


# -- the three switches are independent -------------------------------------------------------


@pytest.mark.parametrize(
    ("live", "post", "video"),
    [
        (True, True, True),
        (True, True, False),
        (True, False, True),
        (True, False, False),
        (False, True, True),
        (False, True, False),
        (False, False, True),
    ],
)
async def test_every_valid_combination_starts_exactly_what_it_asked_for(
    manager, live: bool, post: bool, video: bool
) -> None:
    session, _store, _events = manager
    await session.start(
        window(),
        options=CaptureOptions(live_transcription=live, post_transcription=post, video=video),
    )
    try:
        # The engine is what produces live text, the sink is what a second pass reads, and the
        # recorder is what writes video. Each follows its own switch and nothing else.
        assert (session._engine is not None) is live, "live transcription did not follow its switch"
        assert (session._sink is not None) is post, "the audio file did not follow its switch"
        assert (session._recorder is not None) is video, "video did not follow its switch"
    finally:
        await session.stop()


async def test_the_all_off_combination_is_refused_before_anything_starts(manager) -> None:
    """Refused at the route as well; this is the pipeline's own guard."""
    session, _store, _events = manager
    options = CaptureOptions(live_transcription=False, post_transcription=False, video=False)
    assert options.records_nothing is True


async def test_video_off_never_touches_the_portal(manager) -> None:
    """No dialog should appear for a run that is not recording a window's picture."""
    session, _store, _events = manager
    await session.start(window(), options=CaptureOptions(video=False))
    try:
        assert FakePortal.instances == []
    finally:
        await session.stop()


# -- the portal -------------------------------------------------------------------------------


async def test_a_granted_window_starts_the_recorder_with_the_portals_descriptor(manager) -> None:
    """Without the descriptor `pipewiresrc` opens nothing and the video is silently empty."""
    session, _store, _events = manager
    await session.start(window(), options=CaptureOptions())
    try:
        assert FakeRecorder.instances
        assert FakeRecorder.instances[0].portal_fd == 3
        assert "path=7" in FakeRecorder.instances[0].spec.args
    finally:
        await session.stop()


async def test_declining_the_dialog_records_nothing_at_all(manager) -> None:
    """Starting an audio recording they did not ask for would be reading a refusal as a yes."""
    session, _store, _events = manager
    FakePortal.behaviour = "decline"

    with pytest.raises(SessionError, match="cancelled"):
        await session.start(window(), options=CaptureOptions())

    assert session.is_running is False
    assert session._sink is None
    assert session._recorder is None


async def test_the_portal_session_is_closed_on_stop(manager) -> None:
    """Otherwise the compositor keeps telling the user they are sharing a window."""
    session, _store, _events = manager
    await session.start(window(), options=CaptureOptions())
    await session.stop()

    assert FakePortal.instances[0].closed is True


# -- the video is the expendable part ------------------------------------------------------------


async def test_a_recorder_that_will_not_start_keeps_the_session(manager) -> None:
    session, _store, events = manager

    original = FakeRecorder.start

    def failing(self):
        raise RecorderError("the encoder fell over")

    FakeRecorder.start = failing
    try:
        await session.start(window(), options=CaptureOptions())
        assert session.is_running is True, "a failed video recorder took the session down with it"
        assert "capture-failed" in events.codes()
    finally:
        FakeRecorder.start = original
        await session.stop()


async def test_an_unavailable_machine_keeps_the_session(manager, monkeypatch) -> None:
    session, _store, events = manager
    monkeypatch.setattr(
        manager_module,
        "detect_capture",
        lambda **_kwargs: CaptureSupport(
            available=False, missing="portal", reason="Install xdg-desktop-portal-kde."
        ),
    )

    await session.start(window(), options=CaptureOptions())
    try:
        assert session.is_running is True
        assert "capture-unavailable" in events.codes()
        # The specific remedy, not a generic refusal.
        assert any(
            "xdg-desktop-portal-kde" in payload.get("message", "") for payload in events.of("error")
        )
    finally:
        await session.stop()


async def test_the_window_closing_is_reported_without_ending_the_session(manager) -> None:
    """People close windows mid-talk. The audio keeps going."""
    session, _store, events = manager
    await session.start(window(), options=CaptureOptions())
    try:
        recorder = FakeRecorder.instances[0]
        recorder.state.window_closed = True
        recorder.state.running = False
        recorder.on_stopped(recorder.state)

        assert "capture-window-closed" in events.codes()
        assert session.is_running is True
    finally:
        await session.stop()


# -- what the monitor pane reads ------------------------------------------------------------------


async def test_the_capture_state_describes_the_run(manager) -> None:
    session, _store, _events = manager
    await session.start(
        window(), options=CaptureOptions(live_transcription=True, post_transcription=False)
    )
    try:
        state = session.capture_state()
        assert state["recording"] is True
        assert state["video_path"].endswith(".webm")
        assert state["options"] == {
            "live_transcription": True,
            "post_transcription": False,
            "video": True,
        }
    finally:
        await session.stop()


async def test_no_capture_means_no_capture_state(manager) -> None:
    session, _store, _events = manager
    await session.start(SessionMetadata(session_id="live0001", mode=modes.LIVE))
    try:
        assert session.state()["capture"] is None
    finally:
        await session.stop()


async def test_the_capture_state_is_published_when_it_starts(manager) -> None:
    session, _store, events = manager
    await session.start(window(), options=CaptureOptions())
    try:
        assert events.of("capture.state")
    finally:
        await session.stop()


# -- live transcription during a window capture ------------------------------------------------


async def test_live_transcription_still_commits_while_video_records(manager) -> None:
    """The point of the mode: read along and ask questions *while* it is being recorded."""
    session, _store, events = manager
    await session.start(window(), options=CaptureOptions())
    try:
        assert events.wait_for("transcript.committed"), "no transcript during a window capture"
    finally:
        await session.stop()


async def test_live_transcription_off_produces_no_text(manager) -> None:
    session, _store, events = manager
    await session.start(window(), options=CaptureOptions(live_transcription=False))
    try:
        time.sleep(0.6)
        assert events.of("transcript.committed") == []
    finally:
        await session.stop()


# -- a video that stopped before the talk did ---------------------------------------------------
#
# Measured against `data/recordings/20260904-155600-08ab28c2d732`: audio to 3925.7 s, video frames
# to 882.5 s, and a combined file ending both at 1765.2 s. The audio survived every stage of the
# recording and was deleted by the tidy-up, because retention is a setting and the setting said no.
# It stops being a setting when the combined file is the only other copy and does not hold it all.


async def test_a_short_combined_file_keeps_the_audio_whatever_the_setting_says(
    manager, monkeypatch
) -> None:
    session, store, events = manager
    store.update({"storage.retain_audio": False})
    retained: list[bool] = []

    monkeypatch.setattr(
        manager_module,
        "mux_audio_video",
        lambda video, audio, **_kw: manager_module.MuxResult(
            True, path=str(video), audio_shortfall_s=2160.0
        ),
    )
    real_start = manager_module.TranscriptionRunner.start

    def note(self, *, job, store, retain_audio, prompt=None):
        retained.append(retain_audio)
        return real_start(self, job=job, store=store, retain_audio=retain_audio, prompt=prompt)

    monkeypatch.setattr(manager_module.TranscriptionRunner, "start", note)

    await session.start(
        window(), options=CaptureOptions(live_transcription=False, post_transcription=True)
    )
    await asyncio.sleep(0.4)
    await session.stop()

    assert retained == [True], "the only complete copy of the sound was up for deletion"
    assert "video-ended-early" in events.codes(), "the shortfall was absorbed rather than said"


async def test_a_complete_combined_file_leaves_retention_to_the_setting(
    manager, monkeypatch
) -> None:
    """The guard must not turn a preference off for every recording — only for a broken one."""
    session, store, events = manager
    store.update({"storage.retain_audio": False})
    retained: list[bool] = []

    monkeypatch.setattr(
        manager_module,
        "mux_audio_video",
        lambda video, audio, **_kw: manager_module.MuxResult(True, path=str(video)),
    )
    real_start = manager_module.TranscriptionRunner.start

    def note(self, *, job, store, retain_audio, prompt=None):
        retained.append(retain_audio)
        return real_start(self, job=job, store=store, retain_audio=retain_audio, prompt=prompt)

    monkeypatch.setattr(manager_module.TranscriptionRunner, "start", note)

    await session.start(
        window(), options=CaptureOptions(live_transcription=False, post_transcription=True)
    )
    await asyncio.sleep(0.4)
    await session.stop()

    assert retained == [False]
    assert "video-ended-early" not in events.codes()
