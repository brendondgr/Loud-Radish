"""Stand-ins for the two things a window session talks to that a test cannot have.

The real portal puts the compositor's own picker on screen and waits for a human, and the real
recorder is a GStreamer subprocess reading a PipeWire node that only exists because that human said
yes. Both are exercised for real by hand — see `docs/workflow.md` — and replaced here.

Shared by `test_window_session.py` (what survives when the video dies) and `test_capture_resume.py`
(what happens next). One copy, because two would drift and the tests that used the stale one would
go on passing.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
from app.services.audio.formats import SAMPLE_RATE
from app.services.capture import CaptureSupport, PortalDeclined, RecorderError, WindowStream
from app.services.session import manager as manager_module


class Recorder:
    """Collects everything the session emitted, from whichever thread emitted it."""

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

    def __init__(
        self, spec, *, portal_fd=None, on_stopped=None, on_health=None, log_dir=None, log_name=""
    ) -> None:
        self.spec = spec
        self.portal_fd = portal_fd
        self.on_stopped = on_stopped
        self.on_health = on_health
        self.log_name = log_name
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


class Credentials:
    """The OS credential store, holding the one thing a resumed capture needs from it."""

    def __init__(self, token: str = "stored-token") -> None:
        self.values = {"capture-restore-token": token} if token else {}

    def get(self, key: str) -> str:
        return self.values.get(key, "")

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


def write_talk_wav(path: Path, seconds: float = 6.0) -> Path:
    """Something with a voice's shape in it, so the VAD and the engine have work to do."""
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


def install(monkeypatch) -> None:
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
