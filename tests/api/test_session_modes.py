"""Capture mode carried through the session API (D-020).

Two things are being protected here. The first is that the mode reaches the session and comes back
out again — in the start response, in ``GET /api/session``, and in the ``session.started`` payload —
because every part of the interface reads it from one of those three and a mode that is accepted and
then forgotten produces a header showing the wrong thing.

The second is the one that matters more: **live transcription is unchanged.** This whole expansion
renames the existing behaviour before adding to it, and the way it can fail invisibly is by altering
what it renamed. The end-to-end test at the bottom drives a real session in ``live`` mode over the
file source and asserts segments still commit.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.main import create_app
from app.services.audio.formats import SAMPLE_RATE
from app.services.session import SessionManager, modes
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    app = create_app(config=ConfigStore(config_path=tmp_path / "config.json"))
    with TestClient(app) as client:
        yield client


# -- the schema accepts the vocabulary and nothing else ---------------------------------


def test_start_defaults_to_live_when_no_mode_is_given(client: TestClient) -> None:
    """A client written before capture modes existed must keep working."""
    response = client.post("/api/session/start", json={})
    # Either it started, or it failed for a reason that is not the mode — a headless machine has no
    # capture device. Both prove the field defaulted rather than being required.
    assert response.status_code != 422
    if response.status_code == 200:
        assert response.json()["session"]["mode"] == modes.LIVE


def test_an_unknown_mode_is_a_validation_error(client: TestClient) -> None:
    response = client.post("/api/session/start", json={"mode": "screen"})
    assert response.status_code == 422


@pytest.mark.parametrize("mode", list(modes.CAPTURE_MODES))
def test_no_mode_is_refused_as_unimplemented_any_more(client: TestClient, mode: str) -> None:
    """All three plans removed their own entry. The set is empty and the mechanism is kept.

    A mode can still fail to *start* — no capture device, no portal, a declined dialog — but that
    is a 409 about this machine, not a 501 about this build.
    """
    response = client.post("/api/session/start", json={"mode": mode})
    assert response.status_code != 501
    if response.status_code == 200:
        assert response.json()["session"]["mode"] == mode
        client.post("/api/session/stop")


def test_a_start_that_fails_leaves_nothing_running(client: TestClient) -> None:
    """Whether window capture can start here depends on the machine; either way a failure must
    not leave a half-built session behind."""
    response = client.post("/api/session/start", json={"mode": modes.WINDOW})
    if response.status_code != 200:
        assert client.get("/api/session").json()["running"] is False
    else:
        client.post("/api/session/stop")


# -- per-run options --------------------------------------------------------------------


def test_all_options_off_is_refused_as_recording_nothing(client: TestClient) -> None:
    """Refused ahead of the not-built-yet check, so the message names the real problem."""
    response = client.post(
        "/api/session/start",
        json={
            "mode": modes.WINDOW,
            "options": {"live_transcription": False, "post_transcription": False, "video": False},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "records-nothing"


@pytest.mark.parametrize(
    "options",
    [
        {"live_transcription": True, "post_transcription": False, "video": False},
        {"live_transcription": False, "post_transcription": True, "video": False},
        {"live_transcription": False, "post_transcription": False, "video": True},
        {"live_transcription": True, "post_transcription": True, "video": True},
    ],
)
def test_every_other_combination_gets_past_validation(client: TestClient, options: dict) -> None:
    """Video with no transcription, and transcription with no video, are both legitimate."""
    response = client.post("/api/session/start", json={"mode": modes.WINDOW, "options": options})
    # It may still fail to start on this machine — no device, no portal, a declined dialog — but
    # never with 422, which would mean the combination itself was rejected.
    assert response.status_code != 422
    if response.status_code == 200:
        client.post("/api/session/stop")


def test_options_are_optional(client: TestClient) -> None:
    """Absent means the defaults; it is not a required field."""
    response = client.post("/api/session/start", json={"mode": modes.WINDOW})
    assert response.status_code != 422
    if response.status_code == 200:
        client.post("/api/session/stop")


# -- mode availability, which is what the interface disables a mode from -----------------


def test_health_reports_availability_for_every_mode(client: TestClient) -> None:
    reported = client.get("/api/health").json()["modes"]
    assert set(reported) == set(modes.CAPTURE_MODES)
    for mode, entry in reported.items():
        assert isinstance(entry["available"], bool), mode
        assert isinstance(entry["missing"], list), mode


def test_an_unavailable_mode_names_a_remedy(client: TestClient) -> None:
    """A mode disabled with no reason is indistinguishable from one that does not exist."""
    reported = client.get("/api/health").json()["modes"]
    for mode, entry in reported.items():
        if not entry["available"]:
            assert entry["missing"], f"{mode} is unavailable but names nothing missing"
            assert entry["reason"], f"{mode} is unavailable with no remedy"


def test_window_capture_availability_is_probed_rather_than_assumed(client: TestClient) -> None:
    """It depends on the machine — a portal, GStreamer, and an encoder — so it is measured.

    Deliberately does not assert *which* answer: this suite has to pass on a headless CI box and on
    the KDE Wayland desktop the feature was built for, and those give opposite results. What must
    hold either way is that the two reports agree with each other and that an unavailable mode
    still names its remedy.
    """
    body = client.get("/api/health").json()
    supported = body["optional"]["window_capture"]

    assert isinstance(supported, bool)
    assert body["modes"][modes.WINDOW]["available"] is supported
    assert body["capture"]["available"] is supported
    if not supported:
        assert body["capture"]["missing"], "unavailable without naming which piece"
        assert body["modes"][modes.WINDOW]["reason"], "unavailable without a remedy"


# -- live mode is unchanged --------------------------------------------------------------


class Recorder:
    """Collects emitted transport events, exactly as the hub will."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, name: str, data: dict) -> None:
        with self._lock:
            self.events.append((name, data))

    def of(self, name: str) -> list[dict]:
        with self._lock:
            return [data for event, data in self.events if event == name]

    def wait_for(self, name: str, timeout: float = 6.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.of(name):
                return True
            time.sleep(0.02)
        return False


def write_talk_wav(path: Path, seconds: float = 6.0) -> Path:
    """A syllable-modulated tone — realistic enough for the VAD to call it speech."""
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    tone = (
        0.45 * np.sin(2 * np.pi * 180 * t)
        + 0.28 * np.sin(2 * np.pi * 420 * t)
        + 0.14 * np.sin(2 * np.pi * 950 * t)
    )
    syllables = 0.12 + 0.88 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t))
    signal = np.clip(tone * syllables * 0.35, -1.0, 1.0)

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def manager(tmp_path: Path) -> tuple[SessionManager, Recorder]:
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav")),
            "audio.file_speed": 0.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
        }
    )
    recorder = Recorder()
    return SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions"), recorder


@pytest.mark.anyio
async def test_a_live_session_still_transcribes_end_to_end(manager) -> None:
    """The regression that matters: renaming the existing behaviour did not change it."""
    session, recorder = manager
    await session.start()
    try:
        assert recorder.wait_for("transcript.committed"), "no segment committed in live mode"
    finally:
        await session.stop()

    committed = recorder.of("transcript.committed")
    assert committed, "live mode produced no transcript"
    # Ids are contiguous from 1 and text is non-empty — the two properties every downstream
    # consumer (store, search, export, citations) depends on.
    assert [segment["id"] for segment in committed] == list(range(1, len(committed) + 1))
    assert all(segment["text"].strip() for segment in committed)


@pytest.mark.anyio
async def test_the_started_event_carries_the_mode(manager) -> None:
    session, recorder = manager
    await session.start()
    try:
        started = recorder.of("session.started")
        assert started and started[0]["mode"] == modes.LIVE
    finally:
        await session.stop()


@pytest.mark.anyio
async def test_the_session_state_reports_the_mode(manager) -> None:
    session, _ = manager
    await session.start()
    try:
        assert session.state()["session"]["mode"] == modes.LIVE
    finally:
        await session.stop()
