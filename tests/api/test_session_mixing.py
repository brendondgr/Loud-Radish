"""A session with two audio inputs, mixed (D-081).

Driven end to end over the file source, which stands in for a run's first input — the second is
then silence — so the whole path runs: the mixer, the live engine, the mixed WAV, each input's own
track, the state the interface reads, and the message when one input is lost. Real devices are a
real-desktop check (Phase 7 of `docs/plans/mixed-audio-and-steady-video.md`).
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.session import SessionMetadata
from app.services.recording import layout_for
from app.services.session import CaptureOptions, SessionManager, modes
from fastapi.testclient import TestClient

from tests.api.test_session_modes import Recorder, write_talk_wav


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _store(tmp_path: Path, **overrides) -> ConfigStore:
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav", seconds=3.0)),
            "audio.file_speed": 0.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "recording.recording_dir": str(tmp_path / "recordings"),
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
            **overrides,
        }
    )
    return store


@pytest.fixture
def manager(tmp_path: Path):
    recorder = Recorder()
    store = _store(tmp_path, **{"storage.retain_audio": True})
    return SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions"), recorder


def _both(**extra) -> CaptureOptions:
    """Computer sound and the microphone, transcribed live and afterwards."""
    return CaptureOptions(
        source="system",
        live_transcription=True,
        post_transcription=True,
        video=False,
        audio_inputs=("system", "microphone"),
        **extra,
    )


def _samples(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16)


async def _record(session: SessionManager, recorder: Recorder, options, session_id: str):
    await session.start(SessionMetadata(session_id=session_id, mode=None), options=options)
    try:
        # The file plays as fast as it can be read, so it has ended by the time this returns.
        recorder.wait_for("transcript.committed")
        state = session.state()
        layout = layout_for(
            session._config.resolve().recording.recording_dir,
            session._metadata.started_at,
            session_id,
        )
    finally:
        await session.stop()
    return state, layout


# -- the run ---------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_two_inputs_record_and_transcribe_from_the_mix(tmp_path: Path) -> None:
    """Read while the file is still playing — at real time, so the mix is still running."""
    recorder = Recorder()
    store = _store(tmp_path, **{"audio.file_speed": 1.0})
    session = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")
    await session.start(
        SessionMetadata(session_id="mixboth", mode=None), options=_both(levels={"system": 80})
    )
    try:
        state = session.state()
        assert recorder.wait_for("transcript.committed"), "nothing was transcribed from the mix"
    finally:
        await session.stop()

    assert list(state["options"]["audio_inputs"]) == ["system", "microphone"]
    mix = state["mix"]
    assert mix["inputs"] == ["system", "microphone"]
    # The file stands in for the first input and keeps the time.
    assert mix["primary"] == "system"
    assert mix["levels"]["system"]["volume"] == 80
    assert mix["levels"]["microphone"]["volume"] == 100
    assert mix["levels"]["microphone"]["muted"] is False
    started = recorder.of("session.started")
    assert started[0]["mix"]["inputs"] == ["system", "microphone"]


@pytest.mark.anyio
async def test_each_input_keeps_its_own_track_beside_the_mix(manager) -> None:
    session, recorder = manager
    _, layout = await _record(session, recorder, _both(), "mixtracks")

    system = _samples(layout.input_track("system"))
    microphone = _samples(layout.input_track("microphone"))
    mixed = _samples(layout.audio)
    assert system.size == microphone.size == mixed.size > 0
    # The second input is silence on a test server, so the mix is exactly the first input.
    assert np.array_equal(mixed, system)
    assert not microphone.any()


@pytest.mark.anyio
async def test_no_tracks_are_kept_when_audio_is_not(tmp_path: Path) -> None:
    """Someone who chose not to keep audio must not find their microphone kept on its own."""
    recorder = Recorder()
    store = _store(tmp_path, **{"storage.retain_audio": False})
    session = SessionManager(store, emit=recorder, session_dir=tmp_path / "sessions")
    _, layout = await _record(session, recorder, _both(), "mixnokeep")

    assert not layout.input_track("system").exists()
    assert not layout.input_track("microphone").exists()


@pytest.mark.anyio
async def test_a_muted_input_is_silent_in_its_track_and_in_the_mix(manager) -> None:
    """Mute applies to everything kept (D-081)."""
    session, recorder = manager
    _, layout = await _record(session, recorder, _both(levels={"system": 0}), "mixmuted")

    assert not _samples(layout.input_track("system")).any()
    assert not _samples(layout.audio).any()


@pytest.mark.anyio
async def test_a_single_input_recording_is_unchanged_by_the_mixer(
    tmp_path: Path, monkeypatch
) -> None:
    """Every run goes through the mixer now; one input must come out sample for sample."""
    options = CaptureOptions(
        source="microphone", live_transcription=False, post_transcription=True, video=False
    )

    async def record(name: str, bypass: bool) -> np.ndarray:
        recorder = Recorder()
        store = _store(tmp_path / name, **{"storage.retain_audio": True})
        session = SessionManager(store, emit=recorder, session_dir=tmp_path / name / "sessions")
        if bypass:
            monkeypatch.setattr(
                SessionManager, "_build_mix", lambda self, config, inputs: inputs[0].source
            )
        _, layout = await _record(session, recorder, options, name)
        monkeypatch.undo()
        return _samples(layout.audio)

    (tmp_path / "plain").mkdir()
    (tmp_path / "mixed").mkdir()
    plain = await record("plain", bypass=True)
    mixed = await record("mixed", bypass=False)
    assert plain.size > 0
    assert np.array_equal(plain, mixed)


@pytest.mark.anyio
async def test_a_lost_input_is_said_and_the_rest_keeps_recording(manager) -> None:
    session, recorder = manager
    await session.start(SessionMetadata(session_id="mixlost", mode=None), options=_both())
    try:
        session._mix._on_input_error("microphone", OSError("unplugged"))
        errors = [event for event in recorder.of("error") if event["code"] == "audio-input-lost"]
        assert errors and "Your microphone stopped" in errors[0]["message"]
        assert session.is_running
        assert session.state()["mix"]["levels"]["microphone"]["alive"] is False
        assert recorder.of("mix.changed")
    finally:
        await session.stop()


# -- the API ---------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(config=_store(tmp_path))
    with TestClient(app) as client:
        yield client


def test_a_two_input_start_is_reported_back(client: TestClient) -> None:
    response = client.post(
        "/api/session/start",
        json={
            "options": {
                "source": "system",
                "live_transcription": True,
                "post_transcription": False,
                "audio_inputs": ["system", "microphone"],
                "levels": {"microphone": 60},
            }
        },
    )
    assert response.status_code == 200, response.text
    try:
        state = client.get("/api/session").json()
        assert state["options"]["audio_inputs"] == ["system", "microphone"]
        assert state["mix"]["levels"]["microphone"]["volume"] == 60
    finally:
        client.post("/api/session/stop")


@pytest.mark.parametrize(
    "options",
    [
        {"source": "window", "audio_inputs": ["window", "system"]},
        {"source": "microphone", "audio_inputs": ["system"]},
        {"source": "system", "audio_inputs": ["system"], "levels": {"microphone": 10}},
        {"source": "system", "audio_inputs": ["system"], "levels": {"system": 101}},
    ],
)
def test_a_combination_that_cannot_be_recorded_is_a_conflict(client: TestClient, options) -> None:
    response = client.post("/api/session/start", json={"options": options})
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "options-conflict"
    assert client.get("/api/session").json()["running"] is False


@pytest.mark.parametrize(
    "options",
    [
        {"source": "system", "audio_inputs": []},
        {"source": "system", "audio_inputs": ["system", "microphone", "window"]},
        {"source": "system", "audio_inputs": ["speakers"]},
    ],
)
def test_a_malformed_input_list_is_a_validation_error(client: TestClient, options) -> None:
    assert client.post("/api/session/start", json={"options": options}).status_code == 422


def test_nothing_is_reported_as_mixing_when_nothing_runs(client: TestClient) -> None:
    assert client.get("/api/session").json()["mix"] is None
    assert modes.MAX_AUDIO_INPUTS == 2
