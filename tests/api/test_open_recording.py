"""Bringing a past recording back to the main screen (D-076).

Opening a recording moves its store into the slot D-031 keeps the last session in, so every reader
of `SessionManager.store` — the transcript routes, the conversation, the session state — reads it
without having been told anything. These check that through the HTTP surface the page uses, plus
the refusals, closing, and the delete that now removes the recording folder as well.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.transcript.store import TranscriptStore
from fastapi.testclient import TestClient

TALK = "20261102-100000-a1a1a1a1a1a1"
EMPTY = "20261104-100000-c3c3c3c3c3c3"


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
            # Off, so the start-up reopen (D-041) does not pre-fill the slot under test.
            "storage.reopen_last_session": False,
        }
    )
    with TestClient(create_app(config=store)) as client:
        yield client


def write(sessions: Path, key: str, *, segments: int = 3, chat: bool = False) -> Path:
    path = sessions / f"{key}.db"
    metadata = SessionMetadata(session_id=key.rsplit("-", 1)[1], title=f"Talk {key[-4:]}")
    with TranscriptStore(path, metadata=metadata) as store:
        for index in range(segments):
            store.append_segment(
                Segment(
                    id=index + 1, text=f"sentence {index}", start=index * 5.0, end=index * 5.0 + 4
                )
            )
        if chat:
            store.add_chat_message("user", "What was the first point?")
            store.add_chat_message("assistant", "That a sentence came first.")
    return path


# -- opening -------------------------------------------------------------------------------------


def test_an_opened_recording_is_what_the_main_screen_reads(client, dirs) -> None:
    write(dirs[0], TALK, chat=True)

    state = client.post(f"/api/sessions/{TALK}/open").json()

    assert state["viewing"]["key"] == TALK
    assert state["viewing"]["title"] == f"Talk {TALK[-4:]}"
    assert state["stats"]["segments"] == 3
    # The same routes the page re-reads on a reload — none of them was told about viewing.
    segments = client.get("/api/transcript/since/0").json()["segments"]
    assert [segment["text"] for segment in segments] == ["sentence 0", "sentence 1", "sentence 2"]
    history = client.get("/api/chat/history").json()["messages"]
    assert [message["text"] for message in history][0] == "What was the first point?"
    assert client.get("/api/session").json()["viewing"]["key"] == TALK


def test_opening_another_recording_replaces_the_first(client, dirs) -> None:
    other = "20261103-100000-b2b2b2b2b2b2"
    write(dirs[0], TALK)
    write(dirs[0], other, segments=1)

    client.post(f"/api/sessions/{TALK}/open")
    state = client.post(f"/api/sessions/{other}/open").json()

    assert state["viewing"]["key"] == other
    assert state["stats"]["segments"] == 1


def test_a_recording_without_a_transcript_is_refused_with_the_way_forward(client, dirs) -> None:
    write(dirs[0], EMPTY, segments=0)

    response = client.post(f"/api/sessions/{EMPTY}/open")

    assert response.status_code == 409
    error = response.json()["detail"]["error"]
    assert error["code"] == "needs-transcription"
    assert "Transcribe it first" in error["message"]
    assert client.get("/api/session").json()["viewing"] is None


def test_an_unknown_recording_is_a_404(client) -> None:
    assert client.post(f"/api/sessions/{TALK}/open").status_code == 404


def test_opening_is_refused_while_a_session_records(client, dirs) -> None:
    write(dirs[0], TALK)
    manager = client.app.state.session_manager
    manager._metadata = SessionMetadata(session_id="live")
    try:
        response = client.post(f"/api/sessions/{TALK}/open")
        assert response.status_code == 409
        assert response.json()["detail"]["error"]["code"] == "session-running"
    finally:
        manager._metadata = None


def test_opening_is_refused_while_a_transcription_runs(client, dirs, monkeypatch) -> None:
    write(dirs[0], TALK)
    jobs = client.app.state.session_manager.jobs
    monkeypatch.setattr(type(jobs), "is_busy", property(lambda _self: True))

    response = client.post(f"/api/sessions/{TALK}/open")

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "transcription-running"


# -- closing and what ends a view ----------------------------------------------------------------


def test_close_empties_the_main_screen(client, dirs) -> None:
    write(dirs[0], TALK)
    client.post(f"/api/sessions/{TALK}/open")

    state = client.post("/api/sessions/close").json()

    assert state["viewing"] is None
    assert state["stats"] is None
    assert client.app.state.session_manager.store is None


def test_a_new_session_ends_the_view(client, dirs) -> None:
    """`start` releases the retained store; the view is that store, so it has to go too."""
    write(dirs[0], TALK)
    client.post(f"/api/sessions/{TALK}/open")
    manager = client.app.state.session_manager

    manager._release_retained()  # what `start` calls before opening the new session's store

    assert manager.viewing is None
    assert client.get("/api/session").json()["viewing"] is None


def test_the_view_is_announced_to_every_client(tmp_path, dirs) -> None:
    from app.services.session import SessionManager

    events: list[tuple[str, dict]] = []
    store = ConfigStore(config_path=tmp_path / "c.json")
    store.update({"storage.session_dir": str(dirs[0])})
    manager = SessionManager(store, emit=lambda name, payload: events.append((name, payload)))
    path = write(dirs[0], TALK)

    manager.view_recording(path, {"key": TALK, "title": "t", "started_at": ""})
    manager.close_viewing()

    viewing = [payload["viewing"] for name, payload in events if name == "session.viewing"]
    assert viewing == [{"key": TALK, "title": "t", "started_at": ""}, None]


# -- deleting ------------------------------------------------------------------------------------


def test_deleting_removes_the_recording_folder_too(client, dirs) -> None:
    """Otherwise the folder would come straight back as a "not transcribed" recording."""
    sessions, recordings = dirs
    write(sessions, TALK)
    folder = recordings / TALK
    folder.mkdir()
    (folder / "audio.wav").write_bytes(b"RIFF" + bytes(100))

    assert client.delete(f"/api/sessions/{TALK}").json() == {"deleted": True}

    assert not (sessions / f"{TALK}.db").exists()
    assert not folder.exists()


def test_deleting_the_viewed_recording_releases_it_first(client, dirs) -> None:
    write(dirs[0], TALK)
    client.post(f"/api/sessions/{TALK}/open")

    assert client.delete(f"/api/sessions/{TALK}").json() == {"deleted": True}

    assert client.get("/api/session").json()["viewing"] is None
    assert client.app.state.session_manager.store is None


def test_a_folder_with_no_database_can_be_deleted(client, dirs) -> None:
    folder = dirs[1] / EMPTY
    folder.mkdir()
    (folder / "video.webm").write_bytes(b"\x1a\x45\xdf\xa3" + bytes(50))

    assert client.delete(f"/api/sessions/{EMPTY}").json() == {"deleted": True}
    assert not folder.exists()
