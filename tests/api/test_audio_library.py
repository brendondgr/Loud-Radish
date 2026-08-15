"""The recording library — listing, uploading, and refusing.

This endpoint exists because a browser cannot resolve a path on the server's filesystem, so without
it choosing a recording means editing a config file by hand. That makes the *refusals* as important
as the happy path: an upload endpoint that accepts an arbitrary destination is a directory traversal
whether or not anything is listening on the network.
"""

from __future__ import annotations

import wave

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.services.audio import library
from fastapi.testclient import TestClient


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    """Point the library at a temporary directory rather than the developer's own."""
    directory = tmp_path / "audio"
    directory.mkdir()
    monkeypatch.setattr(library, "LIBRARY_DIR", directory)
    return directory


@pytest.fixture
def client(tmp_path, audio_dir):
    app = create_app(config=ConfigStore(config_path=tmp_path / "config.json"))
    with TestClient(app) as client:
        yield client


def wav_bytes(seconds: float = 0.5, rate: int = 16_000) -> bytes:
    """A minimal but genuinely valid mono 16-bit WAV."""
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


# -- listing -----------------------------------------------------------------------------


def test_an_empty_library_still_reports_where_to_put_things(client: TestClient, audio_dir) -> None:
    body = client.get("/api/audio/files").json()

    assert body["files"] == []
    assert body["directory"] == str(audio_dir)


def test_a_configured_file_outside_the_library_is_still_listed(
    client: TestClient, tmp_path
) -> None:
    """Omitting it makes the interface look unset while the pipeline still points at it."""
    outside = tmp_path / "elsewhere.wav"
    outside.write_bytes(wav_bytes())
    client.patch("/api/config", json={"changes": {"audio.file_path": str(outside)}})

    files = client.get("/api/audio/files").json()["files"]

    assert [file["name"] for file in files] == ["elsewhere.wav"]
    assert files[0]["in_library"] is False


def test_an_unreadable_file_is_listed_with_its_problem_rather_than_hidden(
    client: TestClient, audio_dir
) -> None:
    """Silently dropping it looks identical to it never having been uploaded."""
    (audio_dir / "truncated.wav").write_bytes(b"RIFF....WAVEfmt not really")

    files = client.get("/api/audio/files").json()["files"]

    assert len(files) == 1
    assert files[0]["usable"] is False
    assert "header" in files[0]["problem"].lower()


# -- upload ------------------------------------------------------------------------------


def test_an_upload_is_stored_and_selected_in_one_step(client: TestClient, audio_dir) -> None:
    response = client.post(
        "/api/audio/files", files={"file": ("talk.wav", wav_bytes(2.0), "audio/wav")}
    )

    assert response.status_code == 200
    stored = response.json()["file"]
    assert stored["name"] == "talk.wav"
    assert stored["duration_seconds"] == pytest.approx(2.0, abs=0.05)
    assert (audio_dir / "talk.wav").is_file()

    # Uploading and then having to pick it from a list is two steps for one intention.
    config = client.get("/api/config").json()["config"]
    assert config["audio"]["source_type"] == "file"
    assert config["audio"]["file_path"] == stored["path"]


def test_an_upload_never_escapes_the_library_directory(client: TestClient, audio_dir) -> None:
    client.post(
        "/api/audio/files",
        files={"file": ("../../../../etc/passwd.wav", wav_bytes(), "audio/wav")},
    )

    written = list(audio_dir.iterdir())
    assert len(written) == 1
    assert written[0].parent == audio_dir
    assert "/" not in written[0].name


def test_a_second_upload_of_the_same_name_does_not_overwrite_the_first(
    client: TestClient, audio_dir
) -> None:
    """The user may have no other copy of the recording."""
    client.post("/api/audio/files", files={"file": ("talk.wav", wav_bytes(1.0), "audio/wav")})
    client.post("/api/audio/files", files={"file": ("talk.wav", wav_bytes(3.0), "audio/wav")})

    names = sorted(entry.name for entry in audio_dir.iterdir())
    assert names == ["talk-2.wav", "talk.wav"]


def test_a_file_that_is_not_a_wav_is_rejected_and_not_left_behind(
    client: TestClient, audio_dir
) -> None:
    """An unusable file in the library appears in the picker, where selecting it fails later."""
    response = client.post(
        "/api/audio/files", files={"file": ("notes.wav", b"this is not audio", "audio/wav")}
    )

    assert response.status_code == 422
    assert "WAV" in response.json()["detail"]["error"]["message"]
    assert list(audio_dir.iterdir()) == []


def test_a_non_wav_extension_is_refused_by_name(client: TestClient, audio_dir) -> None:
    response = client.post(
        "/api/audio/files", files={"file": ("talk.mp3", b"\xff\xfb\x90", "audio/mpeg")}
    )

    assert response.status_code == 422
    assert "ffmpeg" in response.json()["detail"]["error"]["message"]


# -- delete ------------------------------------------------------------------------------


def test_deleting_the_selected_recording_also_clears_the_selection(
    client: TestClient, audio_dir
) -> None:
    """Otherwise a tidy-up becomes a failure the next time the user presses record."""
    path = client.post(
        "/api/audio/files", files={"file": ("talk.wav", wav_bytes(), "audio/wav")}
    ).json()["file"]["path"]

    assert client.delete(f"/api/audio/files?path={path}").json()["removed"] is True
    assert client.get("/api/config").json()["config"]["audio"]["file_path"] is None


def test_delete_refuses_a_path_outside_the_library(client: TestClient, tmp_path) -> None:
    """A delete endpoint that accepts any path is a delete endpoint for the whole filesystem."""
    victim = tmp_path / "important.wav"
    victim.write_bytes(wav_bytes())

    response = client.delete(f"/api/audio/files?path={victim}")

    assert response.status_code == 422
    assert victim.is_file()
