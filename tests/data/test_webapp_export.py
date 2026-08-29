"""Exporting a recording as a self-contained web application.

The export is a promise: a folder that opens in a browser with no server, no install, and no
network. Every test here is that promise stated as an assertion — the archive holds everything the
page needs, nothing it does not, and above all no credential, because a ZIP is exactly the sort of
thing that gets forwarded.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.export import ExportError, build_webapp
from app.services.recording import layout_for
from app.services.transcript.store import TranscriptStore
from fastapi.testclient import TestClient

KEY = "20260829-174113-d60b37a9e3c4"


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
        {
            "storage.session_dir": str(sessions),
            "recording.recording_dir": str(recordings),
            "llm.local.endpoint": "http://localhost:11434/v1",
            "llm.local.model": "llama3.1:8b",
        }
    )
    return store


@pytest.fixture
def client(config):
    app = create_app(config=config)
    with TestClient(app) as client:
        yield client


def write_session(sessions: Path, key: str = KEY, segments: int = 3) -> Path:
    path = sessions / f"{key}.db"
    metadata = SessionMetadata(session_id=key.split("-")[-1], title="Operator theory")
    with TranscriptStore(path, metadata=metadata) as store:
        for index in range(segments):
            store.append_segment(
                Segment(
                    id=index + 1,
                    text=f"sentence number {index} of the talk",
                    start=index * 10.0,
                    end=index * 10.0 + 9.0,
                )
            )
    return path


def write_recording(recordings: Path, key: str = KEY, *, video: bool = True) -> None:
    folder = recordings / key
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio.wav").write_bytes(b"\x00" * 200)
    if video:
        (folder / "video-with-audio.webm").write_bytes(b"a recorded talk" * 100)


def archive_of(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(response.content))


# -- what is in the archive ----------------------------------------------------------------


def test_the_archive_holds_the_page_the_video_and_the_two_documents(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings)

    response = client.get(f"/api/sessions/{KEY}/webapp")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    names = set(archive_of(response).namelist())
    assert f"{KEY}/index.html" in names
    assert f"{KEY}/media/video-with-audio.webm" in names
    assert f"{KEY}/data/transcript.json" in names
    assert f"{KEY}/data/settings.json" in names
    # Every script the page loads, or it opens to a blank column.
    for script in ("util.js", "layout.js", "player.js", "transcript.js", "assistant.js", "app.js"):
        assert f"{KEY}/{script}" in names, script


def test_the_page_only_references_files_that_are_in_the_archive(client, dirs) -> None:
    """The whole promise is "this folder opens on its own". A missing asset breaks it silently."""
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings)

    zipped = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))
    page = zipped.read(f"{KEY}/index.html").decode()
    names = set(zipped.namelist())

    referenced = re.findall(r'(?:src|href)="([^"]+)"', page)
    for reference in referenced:
        if reference.startswith(("http", "//", "#")):
            pytest.fail(f"the exported page reaches outside the folder: {reference}")
        assert f"{KEY}/{reference}" in names, reference


def test_the_transcript_document_carries_the_talk(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions, segments=4)
    write_recording(recordings)

    zipped = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))
    data = json.loads(zipped.read(f"{KEY}/data/transcript.json"))

    assert data["session"]["title"] == "Operator theory"
    assert len(data["segments"]) == 4
    assert data["media"]["src"] == "media/video-with-audio.webm"
    assert data["media"]["type"] == "video/webm"


def test_the_bundle_carries_the_same_two_documents(client, dirs) -> None:
    """The file:// fallback. A browser will not let a page opened from a file fetch its own data."""
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings)

    zipped = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))
    bundle = zipped.read(f"{KEY}/data/bundle.js").decode()
    transcript = json.loads(zipped.read(f"{KEY}/data/transcript.json"))

    assert bundle.startswith("/*")
    assert "window.EXPORT_BUNDLE = " in bundle
    payload = json.loads(bundle.split("window.EXPORT_BUNDLE = ", 1)[1].rstrip().rstrip(";"))
    assert payload["transcript"] == transcript
    assert payload["settings"]["llm"]["model"] == "llama3.1:8b"


def test_the_settings_document_seeds_the_local_model_and_no_credential(client, dirs) -> None:
    """A ZIP is exactly the sort of thing that gets forwarded. No key ever goes into one."""
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings)

    zipped = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))
    settings = json.loads(zipped.read(f"{KEY}/data/settings.json"))
    raw = zipped.read(f"{KEY}/data/settings.json").decode()

    assert settings["llm"]["endpoint"] == "http://localhost:11434/v1"
    assert settings["llm"]["model"] == "llama3.1:8b"
    assert settings["llm"]["api_key"] == ""
    assert "sk-" not in raw
    assert settings["prompts"]["system"]


def test_the_video_is_stored_rather_than_deflated(client, dirs) -> None:
    """Re-compressing WebM spends minutes of CPU to save a fraction of a percent."""
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings)

    zipped = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))
    info = zipped.getinfo(f"{KEY}/media/video-with-audio.webm")

    assert info.compress_type == zipfile.ZIP_STORED


def test_the_video_survives_the_round_trip_byte_for_byte(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings)
    original = (recordings / KEY / "video-with-audio.webm").read_bytes()

    zipped = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))

    assert zipped.read(f"{KEY}/media/video-with-audio.webm") == original


# -- when it refuses -----------------------------------------------------------------------


def test_a_session_with_no_video_is_refused_with_the_alternative_named(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings, video=False)

    response = client.get(f"/api/sessions/{KEY}/webapp")

    assert response.status_code == 409
    error = response.json()["detail"]["error"]
    assert error["code"] == "not-exportable"
    assert "Markdown" in error["message"]


def test_a_session_with_no_transcript_is_refused(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions, segments=0)
    write_recording(recordings)

    response = client.get(f"/api/sessions/{KEY}/webapp")

    assert response.status_code == 409
    assert "transcription pass" in response.json()["detail"]["error"]["message"]


def test_a_session_with_no_recording_folder_says_there_is_no_video(client, dirs) -> None:
    """A `live` session never wrote a folder. The refusal names what is missing, not the folder."""
    sessions, _ = dirs
    write_session(sessions)

    response = client.get(f"/api/sessions/{KEY}/webapp")

    assert response.status_code == 409
    assert "no video" in response.json()["detail"]["error"]["message"]


@pytest.mark.parametrize("key", ["../../etc/passwd", "not-a-key", "..%2f.."])
def test_a_key_cannot_escape_the_recordings_directory(client, key) -> None:
    response = client.get(f"/api/sessions/{key}/webapp")
    assert response.status_code in (404, 422)


# -- the builder directly --------------------------------------------------------------------


def test_the_builder_names_the_missing_piece(config, dirs) -> None:
    sessions, recordings = dirs
    path = write_session(sessions)
    layout = layout_for(recordings, datetime(2026, 8, 29, 17, 41, 13), "d60b37a9e3c4")
    layout.ensure()

    with TranscriptStore(path) as store, pytest.raises(ExportError, match="no video"):
        build_webapp(
            key=KEY,
            store=store,
            metadata=store.metadata(),
            layout=layout,
            config=config.resolve(),
        )
