"""Changing a recording's volume and mute while it runs (D-081).

The file source plays at real time here, so a change made a moment into the run lands in the middle
of the recording, and the WAV files can be read to see exactly where it took effect.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.main import create_app
from app.models.session import SessionMetadata
from app.services.recording import layout_for
from app.services.session import CaptureOptions, SessionManager
from fastapi.testclient import TestClient

from tests.api.test_session_mixing import _store
from tests.api.test_session_modes import Recorder


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


BOTH = {
    "source": "system",
    "live_transcription": True,
    "post_transcription": True,
    "audio_inputs": ["system", "microphone"],
}


def _samples(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)


@pytest.mark.anyio
async def test_muting_mid_recording_silences_what_follows_in_the_mix_and_the_track(
    tmp_path: Path,
) -> None:
    recorder = Recorder()
    store = _store(tmp_path, **{"audio.file_speed": 1.0, "storage.retain_audio": True})
    session = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")
    options = CaptureOptions(
        source="system",
        live_transcription=False,
        post_transcription=True,
        video=False,
        audio_inputs=("system", "microphone"),
    )
    await session.start(SessionMetadata(session_id="mutemid", mode=None), options=options)
    try:
        time.sleep(1.0)
        state = session.set_levels({"system": {"muted": True}})
        time.sleep(1.0)
        layout = layout_for(
            store.resolve().recording.recording_dir, session._metadata.started_at, "mutemid"
        )
    finally:
        await session.stop()

    assert state["levels"]["system"]["muted"] is True
    assert state["levels"]["system"]["volume"] == 100, "mute must keep the volume"
    changed = recorder.of("mix.changed")
    assert changed and changed[-1]["levels"]["system"]["muted"] is True

    for path in (layout.audio, layout.input_track("system")):
        samples = _samples(path)
        quarter = len(samples) // 4
        assert np.abs(samples[:quarter]).max() > 1000, f"{path.name} had no sound before muting"
        assert not samples[-quarter:].any(), f"{path.name} was not silent after muting"


@pytest.mark.anyio
async def test_lowering_the_volume_lowers_what_follows(tmp_path: Path) -> None:
    recorder = Recorder()
    store = _store(tmp_path, **{"audio.file_speed": 1.0})
    session = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")
    options = CaptureOptions(
        source="system", live_transcription=False, post_transcription=True, video=False
    )
    await session.start(SessionMetadata(session_id="quieter", mode=None), options=options)
    try:
        time.sleep(1.0)
        session.set_levels({"system": {"volume": 25}})
        time.sleep(1.0)
        layout = layout_for(
            store.resolve().recording.recording_dir, session._metadata.started_at, "quieter"
        )
        samples = _samples(layout.audio) if layout.audio.exists() else None
    finally:
        await session.stop()

    # Read before the pass deletes it (retention is off here); a single-input run too.
    assert samples is not None and samples.size
    quarter = len(samples) // 4
    before = np.abs(samples[:quarter].astype(np.int32)).max()
    after = np.abs(samples[-quarter:].astype(np.int32)).max()
    assert after == pytest.approx(before * 0.25, rel=0.1)


@pytest.mark.anyio
async def test_every_input_has_its_own_meter_while_recording(tmp_path: Path) -> None:
    recorder = Recorder()
    store = _store(tmp_path, **{"audio.file_speed": 1.0})
    session = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")
    options = CaptureOptions(
        source="system",
        live_transcription=True,
        post_transcription=False,
        video=False,
        audio_inputs=("system", "microphone"),
    )
    await session.start(SessionMetadata(session_id="meters", mode=None), options=options)
    try:
        assert recorder.wait_for("mix.levels", timeout=3.0)
    finally:
        await session.stop()
    levels = recorder.of("mix.levels")[-1]["levels"]
    assert set(levels) == {"system", "microphone"}
    assert levels["system"]["level"] > 0.0
    assert levels["microphone"]["level"] == 0.0


# -- the API ---------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(config=_store(tmp_path, **{"audio.file_speed": 1.0}))
    with TestClient(app) as client:
        yield client


def test_the_mix_can_be_read_and_changed_over_http(client: TestClient) -> None:
    assert client.post("/api/session/start", json={"options": BOTH}).status_code == 200
    try:
        before = client.get("/api/session/mix").json()
        assert before["levels"]["microphone"] == {**before["levels"]["microphone"], "volume": 100}

        response = client.patch(
            "/api/session/mix",
            json={"inputs": {"microphone": {"volume": 35}, "system": {"muted": True}}},
        )
        assert response.status_code == 200, response.text
        levels = response.json()["levels"]
        assert levels["microphone"]["volume"] == 35
        assert levels["microphone"]["muted"] is False
        assert levels["system"]["muted"] is True

        # Unmuting puts it back where it was rather than at nothing.
        levels = client.patch(
            "/api/session/mix", json={"inputs": {"system": {"muted": False}}}
        ).json()["levels"]
        assert levels["system"] == {**levels["system"], "volume": 100, "muted": False}
        assert client.get("/api/session").json()["mix"]["levels"]["microphone"]["volume"] == 35
    finally:
        client.post("/api/session/stop")


def test_changing_an_input_the_run_does_not_have_changes_nothing(client: TestClient) -> None:
    options = {**BOTH, "audio_inputs": ["system"]}
    assert client.post("/api/session/start", json={"options": options}).status_code == 200
    try:
        response = client.patch(
            "/api/session/mix",
            json={"inputs": {"system": {"volume": 10}, "microphone": {"muted": True}}},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"]["code"] == "unknown-input"
        # All or nothing: the input it does have was not changed either.
        assert client.get("/api/session/mix").json()["levels"]["system"]["volume"] == 100
    finally:
        client.post("/api/session/stop")


@pytest.mark.parametrize(
    "body",
    [
        {"inputs": {"system": {"volume": 150}}},
        {"inputs": {"system": {"volume": -1}}},
        {"inputs": {"speakers": {"volume": 10}}},
        {"inputs": {}},
    ],
)
def test_a_malformed_change_is_a_validation_error(client: TestClient, body) -> None:
    assert client.patch("/api/session/mix", json=body).status_code == 422


def test_nothing_to_change_when_nothing_is_recording(client: TestClient) -> None:
    assert client.get("/api/session/mix").status_code == 409
    response = client.patch("/api/session/mix", json={"inputs": {"system": {"volume": 10}}})
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "no-session"
