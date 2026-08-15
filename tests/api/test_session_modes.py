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


@pytest.mark.parametrize("mode", [modes.WINDOW])
def test_an_unimplemented_mode_is_refused_by_name(client: TestClient, mode: str) -> None:
    """501, not 422 and not 409: the request is valid and the state is fine, the build lacks it."""
    response = client.post("/api/session/start", json={"mode": mode})
    assert response.status_code == 501

    error = response.json()["detail"]["error"]
    assert error["code"] == "mode-unavailable"
    # The message has to say what to do instead, because this is a button the user just pressed.
    assert "not built yet" in error["message"]
    assert "docs/plans/" in error["message"]


def test_recorded_mode_is_no_longer_refused(client: TestClient) -> None:
    """Plan 3 removed its own entry from the unimplemented set."""
    response = client.post("/api/session/start", json={"mode": modes.RECORDED})
    assert response.status_code != 501
    if response.status_code == 200:
        assert response.json()["session"]["mode"] == modes.RECORDED
        client.post("/api/session/stop")


def test_a_refused_mode_does_not_start_anything(client: TestClient) -> None:
    client.post("/api/session/start", json={"mode": modes.WINDOW})
    assert client.get("/api/session").json()["running"] is False


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
    # 501 because window capture is not built; the point is that it was not 422.
    assert response.status_code == 501


def test_options_are_optional(client: TestClient) -> None:
    """Absent means the defaults; it is not a required field for the modes that ignore it."""
    response = client.post("/api/session/start", json={"mode": modes.WINDOW})
    assert response.status_code == 501  # refused for the mode, not for a missing field


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


def test_window_capture_is_reported_absent_until_it_is_built(client: TestClient) -> None:
    # A flat "no" is the honest answer while Plan 4 is outstanding, and is not the same thing as
    # the key being missing — the frontend distinguishes "unavailable" from "unknown".
    assert client.get("/api/health").json()["optional"]["window_capture"] is False
    assert client.get("/api/health").json()["modes"][modes.WINDOW]["available"] is False


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
