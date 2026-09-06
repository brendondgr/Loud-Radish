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


def write_chat(sessions: Path, key: str = KEY) -> None:
    """Three turns of a conversation, of the kind the reported export carried into a shared ZIP."""
    with TranscriptStore(sessions / f"{key}.db") as store:
        store.add_chat_message("user", "So PINNs are used when you have the physics of a system?")
        store.add_chat_message("assistant", "Not quite. The speaker has not framed them that way.")
        store.add_chat_message("user", "For what purpose?", context_timestamp=2239.8)


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


# -- the conversation is not in the shared archive (D-037) --------------------------------------
#
# Reported against the 2026-09-04 seminar, whose database holds six chat messages and every one of
# which rode into `transcript.json`, into `bundle.js` beside it, into the Markdown export, and into
# the JSON export. The exported page then seeded its assistant with them — so a recipient opening
# the ZIP started mid-conversation with someone else's questions about a talk they had not watched,
# which is the opposite of the panel's whole purpose.


def test_the_archive_carries_no_conversation_by_default(client, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions)
    write_chat(sessions)
    write_recording(recordings)

    archive = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))
    transcript = json.loads(archive.read(f"{KEY}/data/transcript.json"))
    bundle = archive.read(f"{KEY}/data/bundle.js").decode("utf-8")

    assert "chat" not in transcript
    # The bundle is the same content again, so a check of one document alone would pass while the
    # conversation shipped in the other — which is exactly how it would be missed.
    assert "PINNs" not in bundle
    assert "PINNs" not in archive.read(f"{KEY}/README.txt").decode("utf-8")


def test_an_export_can_still_carry_it_when_asked(client, dirs) -> None:
    """Off is a default, not a removal. An archive the user keeps may open where they left off."""
    sessions, recordings = dirs
    write_session(sessions)
    write_chat(sessions)
    write_recording(recordings)

    archive = archive_of(client.get(f"/api/sessions/{KEY}/webapp?include_chat=true"))
    transcript = json.loads(archive.read(f"{KEY}/data/transcript.json"))

    assert [message["text"] for message in transcript["chat"]][1].startswith("Not quite")
    assert "PINNs" in archive.read(f"{KEY}/data/bundle.js").decode("utf-8")


def test_the_readme_does_not_promise_a_conversation_that_is_not_there(client, dirs) -> None:
    """A README that lists a thing the archive does not contain will be believed, then contradicted."""
    sessions, recordings = dirs
    write_session(sessions)
    write_chat(sessions)
    write_recording(recordings)

    plain = archive_of(client.get(f"/api/sessions/{KEY}/webapp"))
    with_chat = archive_of(client.get(f"/api/sessions/{KEY}/webapp?include_chat=true"))

    assert "conversation" not in plain.read(f"{KEY}/README.txt").decode("utf-8").lower()
    assert "conversation" in with_chat.read(f"{KEY}/README.txt").decode("utf-8").lower()


def test_a_recording_nobody_asked_about_carries_no_messages_either_way(client, dirs) -> None:
    """The flag decides whether the key is there; it can never invent a conversation."""
    sessions, recordings = dirs
    write_session(sessions)
    write_recording(recordings)

    plain = client.get(f"/api/sessions/{KEY}/webapp")
    asked = client.get(f"/api/sessions/{KEY}/webapp?include_chat=true")

    assert plain.status_code == asked.status_code == 200
    assert "chat" not in json.loads(archive_of(plain).read(f"{KEY}/data/transcript.json"))
    assert json.loads(archive_of(asked).read(f"{KEY}/data/transcript.json"))["chat"] == []


# -- and it exports on its own -------------------------------------------------------------------


def test_the_conversation_exports_as_its_own_document(client, dirs) -> None:
    sessions, _recordings = dirs
    write_session(sessions)
    write_chat(sessions)

    response = client.get(f"/api/sessions/{KEY}/chat?fmt=markdown")

    assert response.status_code == 200
    assert "PINNs" in response.text
    assert "Not quite" in response.text
    assert response.headers["content-disposition"].endswith('-chat.md"')


def test_the_conversation_export_keeps_what_each_answer_was_based_on(client, dirs) -> None:
    """An answer about "the last ten minutes" means nothing without knowing which ten."""
    sessions, _recordings = dirs
    write_session(sessions)
    write_chat(sessions)

    body = client.get(f"/api/sessions/{KEY}/chat?fmt=json").json()

    assert [message["context_timestamp"] for message in body["chat"]][2] == pytest.approx(2239.8)


def test_asking_nothing_is_a_sentence_rather_than_an_error(client, dirs) -> None:
    """"You asked nothing during this recording" is information; a 404 is not."""
    sessions, _recordings = dirs
    write_session(sessions)

    response = client.get(f"/api/sessions/{KEY}/chat?fmt=markdown")

    assert response.status_code == 200
    assert "Nothing was asked" in response.text


def test_an_unknown_conversation_format_is_refused_by_name(client, dirs) -> None:
    sessions, _recordings = dirs
    write_session(sessions)

    response = client.get(f"/api/sessions/{KEY}/chat?fmt=srt")

    assert response.status_code == 422
    assert "markdown" in response.json()["detail"]["error"]["message"]


def test_the_listing_says_how_many_questions_were_asked(client, dirs) -> None:
    """So the conversation is offered as an export only where there is one to export."""
    sessions, _recordings = dirs
    write_session(sessions)
    write_chat(sessions)
    write_session(sessions, key="20260829-180000-aaaaaaaaaaaa")

    rows = {row["key"]: row for row in client.get("/api/sessions").json()["sessions"]}

    assert rows[KEY]["chat_messages"] == 3
    assert rows["20260829-180000-aaaaaaaaaaaa"]["chat_messages"] == 0


def test_the_transcript_export_carries_no_conversation_by_default(client, dirs) -> None:
    """Markdown and JSON were the two formats that could carry it, so they were the two that did."""
    sessions, _recordings = dirs
    write_session(sessions)
    write_chat(sessions)

    markdown = client.get(f"/api/sessions/{KEY}/export?fmt=markdown").text
    payload = client.get(f"/api/sessions/{KEY}/export?fmt=json").json()

    assert "## Conversation" not in markdown
    assert "PINNs" not in markdown
    assert payload["chat"] == []


def test_the_transcript_export_can_still_be_asked_for_one_file(client, dirs) -> None:
    sessions, _recordings = dirs
    write_session(sessions)
    write_chat(sessions)

    markdown = client.get(f"/api/sessions/{KEY}/export?fmt=markdown&include_chat=true").text

    assert "## Conversation" in markdown
    assert "PINNs" in markdown
