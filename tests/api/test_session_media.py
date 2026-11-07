"""What a recording card needs from the sessions API (D-082, redesign Step 2): a picture of each
recording, named downloads, opening the folder, and a line saying what a recording is about.

Each is checked against what the cards rely on: the stored preview when there is one, a real
JPEG when a moment is asked for, a 404 rather than an error for anything missing, and no route
that can be pointed outside the recordings folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.segment import Segment
from app.routes import session_media
from app.services.transcript.glance import glance
from app.services.transcript.store import TranscriptStore
from fastapi.testclient import TestClient

from tests.api.test_transcribe_recording import write_empty_session, write_video, write_wav

KEY = "20261104-100000-c3c3c3c3c3c3"
JPEG = b"\xff\xd8\xff"


@pytest.fixture
def dirs(tmp_path) -> tuple[Path, Path]:
    sessions, recordings = tmp_path / "sessions", tmp_path / "recordings"
    sessions.mkdir()
    recordings.mkdir()
    return sessions, recordings


@pytest.fixture
def client(tmp_path, dirs):
    sessions, recordings = dirs
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "storage.session_dir": str(sessions),
            "recording.recording_dir": str(recordings),
            "storage.reopen_last_session": False,
            "asr.backend": "mock",
        }
    )
    with TestClient(create_app(config=store)) as client:
        yield client


def write_talk(sessions: Path, key: str = KEY) -> Path:
    path = write_empty_session(sessions, key)
    lines = [
        "Today we look at gradient descent and why the learning rate matters.",
        "Gradient descent takes a step against the gradient at every iteration.",
        "Too large a learning rate and gradient descent overshoots the minimum.",
    ]
    with TranscriptStore(path) as store:
        for index, text in enumerate(lines, start=1):
            store.append_segment(
                Segment(id=index, text=text, start=index * 2.0, end=index * 2.0 + 2)
            )
    return path


# -- pictures ------------------------------------------------------------------------------------


def test_the_stored_preview_is_served_when_no_moment_is_asked_for(client, dirs) -> None:
    folder = dirs[1] / KEY
    folder.mkdir()
    (folder / "preview.jpg").write_bytes(JPEG + b"stored")

    response = client.get(f"/api/sessions/{KEY}/thumbnail")

    assert response.status_code == 200
    assert response.content == JPEG + b"stored"
    assert response.headers["content-type"] == "image/jpeg"


def test_a_moment_of_the_video_comes_back_as_a_jpeg(client, dirs) -> None:
    write_video(dirs[1] / KEY / "video.mkv", sound=False, seconds=2)

    response = client.get(f"/api/sessions/{KEY}/thumbnail", params={"t": 1.0, "w": 64})

    assert response.status_code == 200, response.text
    assert response.content.startswith(JPEG)


def test_a_recording_with_no_video_has_no_frame(client, dirs) -> None:
    write_wav(dirs[1] / KEY)
    assert client.get(f"/api/sessions/{KEY}/thumbnail", params={"t": 1}).status_code == 404


@pytest.mark.parametrize("key", ["20990101-000000-000000000000", "..%2F..%2Fetc", "nope"])
def test_an_unknown_or_hostile_key_is_a_404(client, key) -> None:
    assert client.get(f"/api/sessions/{key}/thumbnail").status_code == 404
    assert client.get(f"/api/sessions/{key}/file/video").status_code == 404


# -- downloads -----------------------------------------------------------------------------------


def test_the_video_downloads_under_the_recordings_name(client, dirs) -> None:
    write_video(dirs[1] / KEY / "video.mkv", sound=False)

    response = client.get(f"/api/sessions/{KEY}/file/video")

    assert response.status_code == 200
    assert f'filename="{KEY}-video.mkv"' in response.headers["content-disposition"]


def test_the_audio_downloads_only_while_the_wav_is_still_there(client, dirs) -> None:
    folder = dirs[1] / KEY
    folder.mkdir()
    assert client.get(f"/api/sessions/{KEY}/file/audio").status_code == 404

    write_wav(folder)
    response = client.get(f"/api/sessions/{KEY}/file/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"


def test_only_video_and_audio_can_be_asked_for(client, dirs) -> None:
    write_wav(dirs[1] / KEY)
    assert client.get(f"/api/sessions/{KEY}/file/preview").status_code == 404


# -- the folder ----------------------------------------------------------------------------------


def test_the_folder_opens_in_the_file_manager(client, dirs, monkeypatch) -> None:
    write_wav(dirs[1] / KEY)
    launched: list[list[str]] = []
    monkeypatch.setattr(
        session_media.subprocess, "Popen", lambda command, **_: launched.append(command)
    )

    body = client.post(f"/api/sessions/{KEY}/reveal").json()

    assert body == {"path": str(dirs[1] / KEY), "opened": True}
    assert launched and launched[0][-1] == str(dirs[1] / KEY)


def test_with_no_file_manager_the_path_is_still_returned(client, dirs, monkeypatch) -> None:
    write_wav(dirs[1] / KEY)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("xdg-open")

    monkeypatch.setattr(session_media.subprocess, "Popen", missing)

    body = client.post(f"/api/sessions/{KEY}/reveal").json()
    assert body == {"path": str(dirs[1] / KEY), "opened": False}


# -- what a recording is about -------------------------------------------------------------------


def test_a_glance_names_the_talk_by_its_words(dirs) -> None:
    found = glance(write_talk(dirs[0]))

    assert found.opening.startswith("Today we look at gradient descent")
    assert found.keywords[:2] == ["Gradient", "Descent"]
    assert not {"Today", "Look", "Every"} & set(found.keywords)


def test_glances_are_returned_for_the_keys_that_exist(client, dirs) -> None:
    write_talk(dirs[0])

    body = client.get("/api/sessions/glance", params={"keys": f"{KEY},20990101-000000-x"}).json()

    assert list(body["glances"]) == [KEY]
    assert body["glances"][KEY]["keywords"]


# -- what the listing says -------------------------------------------------------------------------


def test_the_listing_says_whether_a_recording_has_a_preview(client, dirs) -> None:
    write_talk(dirs[0])
    folder = dirs[1] / KEY
    write_wav(folder)

    def media() -> dict:
        listed = client.get("/api/sessions").json()["sessions"]
        return next(item for item in listed if item["key"] == KEY)["media"]

    assert media()["preview"] is False
    (folder / "preview.jpg").write_bytes(JPEG + b"rest")
    assert media()["preview"] is True
