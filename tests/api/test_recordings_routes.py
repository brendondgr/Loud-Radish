"""Listing, re-running, and deleting recordings (D-021).

These endpoints are the recovery path. When a transcription pass fails, the failure message says
"you can run the transcription again" — and without these that sentence has nothing behind it. So
the tests here are weighted towards the refusals: an endpoint that re-runs a pass while one is
already running, or that resolves a path outside the recordings directory, is worse than no
endpoint at all.

A recording is a **directory** now, addressed by its key — ``20260815-120000-abc123`` — so the
helper below writes one the way the session manager does rather than dropping a loose ``.wav``.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.main import create_app
from app.services.audio.formats import SAMPLE_RATE
from fastapi.testclient import TestClient


@pytest.fixture
def recordings_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "recordings"
    directory.mkdir()
    return directory


@pytest.fixture
def client(tmp_path: Path, recordings_dir: Path):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "recording.recording_dir": str(recordings_dir),
            "storage.session_dir": str(tmp_path / "sessions"),
        }
    )
    app = create_app(config=store)
    with TestClient(app) as client:
        yield client


def write_recording(directory: Path, key: str, seconds: float = 2.0) -> Path:
    """Write one recording folder holding ``audio.wav``, as a real session would."""
    folder = directory / key
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "audio.wav"
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    signal = (np.sin(2 * np.pi * 220 * t) * 0.4 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(signal.tobytes())
    return path


# -- listing --------------------------------------------------------------------------------


def test_an_empty_directory_lists_nothing_and_explains_itself(client: TestClient) -> None:
    body = client.get("/api/recordings").json()
    assert body["recordings"] == []
    # The note matters: an empty list is the *normal* state, because a successful pass deletes its
    # audio. Without the explanation it reads as a broken feature.
    assert "deleted once transcribed" in body["note"]


def test_a_recording_is_listed_with_its_duration(client: TestClient, recordings_dir: Path) -> None:
    write_recording(recordings_dir, "20260815-120000-abc123", seconds=3.0)
    entry = client.get("/api/recordings").json()["recordings"][0]

    assert entry["name"] == "20260815-120000-abc123"
    assert entry["audio"] == "audio.wav"
    assert entry["duration_s"] == pytest.approx(3.0, abs=0.05)
    assert entry["bytes"] > 44


def test_recordings_are_listed_newest_first(client: TestClient, recordings_dir: Path) -> None:
    for key in ("20260815-090000-aaaaaa", "20260815-120000-bbbbbb", "20260815-100000-cccccc"):
        write_recording(recordings_dir, key, seconds=0.5)
    names = [entry["name"] for entry in client.get("/api/recordings").json()["recordings"]]
    assert names == sorted(names, reverse=True)


def test_an_unreadable_file_is_listed_rather_than_hidden(
    client: TestClient, recordings_dir: Path
) -> None:
    """Hiding it leaves a file nothing in the interface can explain or remove."""
    folder = recordings_dir / "20260815-120000-abc123"
    folder.mkdir()
    (folder / "audio.wav").write_bytes(b"RIFF....garbage" * 8)
    entry = client.get("/api/recordings").json()["recordings"][0]
    assert entry["name"] == "20260815-120000-abc123"
    assert entry["unreadable"] is True
    assert entry["duration_s"] is None


def test_the_retention_setting_is_reported(client: TestClient) -> None:
    """So the interface can explain *why* audio is accumulating, rather than only that it is."""
    assert client.get("/api/recordings").json()["retain_audio"] is False


# -- path safety -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "subdir/../../escape.wav",
        "20260815-120000-abc123/../../escape",
    ],
)
def test_a_path_outside_the_directory_is_refused(client: TestClient, name: str) -> None:
    """A loopback-bound server is still reachable from any page in any other tab."""
    response = client.delete(f"/api/recordings/{name}")
    assert response.status_code in (404, 422)
    assert response.status_code != 200


def test_a_name_that_is_not_a_recording_key_is_refused(
    client: TestClient, recordings_dir: Path
) -> None:
    (recordings_dir / "notes.txt").write_text("not audio")
    response = client.delete("/api/recordings/notes.txt")
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "bad-recording"


def test_a_missing_recording_is_a_clean_404(client: TestClient) -> None:
    response = client.delete("/api/recordings/20260815-120000-abc123")
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "no-recording"


# -- deleting ---------------------------------------------------------------------------------


def test_a_recording_can_be_deleted(client: TestClient, recordings_dir: Path) -> None:
    key = "20260815-120000-abc123"
    write_recording(recordings_dir, key, seconds=0.5)
    assert client.delete(f"/api/recordings/{key}").json()["removed"] is True
    assert not (recordings_dir / key / "audio.wav").exists()
    assert client.get("/api/recordings").json()["recordings"] == []


def test_deleting_the_audio_keeps_the_video_beside_it(
    client: TestClient, recordings_dir: Path
) -> None:
    """ "Delete this recording's audio" must not quietly take a video with it."""
    key = "20260815-120000-abc123"
    write_recording(recordings_dir, key, seconds=0.5)
    video = recordings_dir / key / "video.webm"
    video.write_bytes(b"a video")

    client.delete(f"/api/recordings/{key}")

    assert video.read_bytes() == b"a video"


# -- re-running a pass --------------------------------------------------------------------------


def test_a_re_run_is_refused_without_a_loaded_model(
    client: TestClient, recordings_dir: Path
) -> None:
    """Naming the remedy, because this is a button the user just pressed."""
    write_recording(recordings_dir, "20260815-120000-abc123", seconds=1.0)
    response = client.post("/api/recordings/20260815-120000-abc123/transcribe")

    assert response.status_code == 409
    error = response.json()["detail"]["error"]
    assert error["code"] == "no-model"
    assert "Settings" in error["message"]


def test_a_re_run_of_an_unreadable_recording_is_refused(
    client: TestClient, recordings_dir: Path
) -> None:
    folder = recordings_dir / "20260815-120000-abc123"
    folder.mkdir()
    (folder / "audio.wav").write_bytes(b"RIFF....garbage" * 8)
    response = client.post("/api/recordings/20260815-120000-abc123/transcribe")
    assert response.status_code in (409, 422)


def test_a_re_run_of_a_missing_recording_is_a_404(client: TestClient) -> None:
    assert client.post("/api/recordings/20260815-120000-abc123/transcribe").status_code == 404


# -- the pass and the session share one model ----------------------------------------------------


def test_a_re_run_is_refused_while_a_session_records(
    client: TestClient, recordings_dir: Path
) -> None:
    """They cannot share the speech model, and the message says so rather than failing obscurely."""
    write_recording(recordings_dir, "20260815-120000-abc123", seconds=1.0)
    started = client.post("/api/session/start", json={})
    if started.status_code != 200:
        pytest.skip("no capture source available in this environment")

    try:
        response = client.post("/api/recordings/20260815-120000-abc123/transcribe")
        assert response.status_code == 409
        assert response.json()["detail"]["error"]["code"] == "session-running"
    finally:
        client.post("/api/session/stop")
