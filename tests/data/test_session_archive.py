"""Past sessions on disk.

The session directory is a user directory. It will contain half-written files from a session that
crashed, files from an older schema, and whatever else is sitting there — so the tests that matter
here are the ones where a file is *not* a well-formed session.
"""

from __future__ import annotations

import sqlite3

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.transcript import archive
from app.services.transcript.store import TranscriptStore
from fastapi.testclient import TestClient


@pytest.fixture
def sessions_dir(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    return directory


@pytest.fixture
def client(tmp_path, sessions_dir):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"storage.session_dir": str(sessions_dir)})
    app = create_app(config=store)
    with TestClient(app) as client:
        yield client


def write_session(directory, name: str, title: str = "", segments: int = 3):
    path = directory / f"{name}.db"
    with TranscriptStore(path, metadata=SessionMetadata(session_id=name, title=title)) as store:
        for index in range(segments):
            store.append_segment(
                Segment(
                    id=index + 1,
                    text=f"a sentence number {index} with several words in it",
                    start=index * 10.0,
                    end=index * 10.0 + 9.0,
                )
            )
    return path


# -- listing -------------------------------------------------------------------------------


def test_a_session_is_described_without_opening_its_transcript(sessions_dir, tmp_path) -> None:
    write_session(sessions_dir, "20260101-1200-abc", title="Operator theory", segments=4)
    config = ConfigStore(config_path=tmp_path / "c.json")
    config.update({"storage.session_dir": str(sessions_dir)})

    listed = archive.list_sessions(config.resolve())

    assert len(listed) == 1
    assert listed[0].title == "Operator theory"
    assert listed[0].segments == 4
    assert listed[0].readable is True


def test_an_untitled_session_gets_a_name_from_its_date(sessions_dir, tmp_path) -> None:
    """Most sessions are untitled, and a list of identical blank rows is unusable."""
    write_session(sessions_dir, "20260101-1200-abc")
    config = ConfigStore(config_path=tmp_path / "c.json")
    config.update({"storage.session_dir": str(sessions_dir)})

    assert archive.list_sessions(config.resolve())[0].title.startswith("Session on ")


def test_an_unreadable_file_is_listed_with_its_reason(client: TestClient, sessions_dir) -> None:
    """A session the user can see and cannot open beats one that has silently vanished."""
    (sessions_dir / "broken.db").write_bytes(b"this is not a database")

    body = client.get("/api/sessions").json()

    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["readable"] is False
    assert body["sessions"][0]["problem"]


def test_a_file_from_an_older_schema_is_listed_rather_than_crashing_the_page(
    client: TestClient, sessions_dir
) -> None:
    path = sessions_dir / "old.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE something_else (id INTEGER)")
    connection.commit()
    connection.close()

    body = client.get("/api/sessions").json()

    assert body["sessions"][0]["readable"] is False


def test_a_missing_session_directory_is_an_empty_list_not_an_error(
    client: TestClient, sessions_dir
) -> None:
    """Before the first recording there is no directory, and that is not a failure."""
    sessions_dir.rmdir()

    assert client.get("/api/sessions").json()["sessions"] == []


def test_sessions_are_newest_first(client: TestClient, sessions_dir) -> None:
    import os
    import time

    first = write_session(sessions_dir, "older", title="Older")
    second = write_session(sessions_dir, "newer", title="Newer")
    os.utime(first, (time.time() - 600, time.time() - 600))

    titles = [s["title"] for s in client.get("/api/sessions").json()["sessions"]]
    assert titles == ["Newer", "Older"]


# -- reading and exporting --------------------------------------------------------------------


def test_a_past_session_can_be_read_after_it_has_stopped(client: TestClient, sessions_dir) -> None:
    """The live transcript routes answer only for the running session; this is the other half."""
    write_session(sessions_dir, "20260101-1200-abc", title="Finished", segments=3)

    body = client.get("/api/sessions/20260101-1200-abc").json()

    assert body["session"]["title"] == "Finished"
    assert len(body["segments"]) == 3
    assert body["stats"]["segments"] == 3


@pytest.mark.parametrize("fmt", ["markdown", "text", "srt", "vtt", "json"])
def test_every_export_format_produces_a_named_download(
    client: TestClient, sessions_dir, fmt: str
) -> None:
    write_session(sessions_dir, "20260101-1200-abc", title="Finished")

    response = client.get(f"/api/sessions/20260101-1200-abc/export?fmt={fmt}")

    assert response.status_code == 200
    assert response.content
    assert "attachment; filename=" in response.headers["content-disposition"]


def test_an_unknown_format_names_what_is_available(client: TestClient, sessions_dir) -> None:
    write_session(sessions_dir, "20260101-1200-abc")

    response = client.get("/api/sessions/20260101-1200-abc/export?fmt=pdf")
    assert response.status_code == 422


def test_a_session_that_is_gone_explains_rather_than_500s(client: TestClient) -> None:
    response = client.get("/api/sessions/never-existed")

    assert response.status_code == 404
    assert "session folder" in response.json()["detail"]["error"]["message"]


# -- safety ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["../../../../etc/passwd", "../secret", "..", "/etc/shadow", "sub/../../escape"],
)
def test_a_key_cannot_escape_the_session_directory(sessions_dir, tmp_path, key) -> None:
    """A key is user input; joining one onto a directory is a traversal waiting to happen.

    Asserted against :func:`archive.find` rather than over HTTP: a URL containing ``..`` is
    normalised by the server before routing, so it never reaches the handler as a key at all — and
    a test that only went through HTTP would be proving the normaliser works, not the guard.
    """
    outside = tmp_path / "secret.db"
    outside.write_bytes(b"not yours")

    config = ConfigStore(config_path=tmp_path / "c.json")
    config.update({"storage.session_dir": str(sessions_dir)})

    assert archive.find(config.resolve(), key) is None


def test_a_legitimate_key_still_resolves(sessions_dir, tmp_path) -> None:
    """The guard must not be so strict that it rejects the keys the listing itself produces."""
    write_session(sessions_dir, "20260101-1200-abc")
    config = ConfigStore(config_path=tmp_path / "c.json")
    config.update({"storage.session_dir": str(sessions_dir)})

    found = archive.find(config.resolve(), "20260101-1200-abc")
    assert found is not None and found.name == "20260101-1200-abc.db"


def test_delete_refuses_the_session_that_is_still_recording(
    client: TestClient, sessions_dir
) -> None:
    """Deleting the file out from under an open connection loses the talk in progress."""
    write_session(sessions_dir, "live-one")
    manager = client.app.state.session_manager
    manager._store = TranscriptStore(sessions_dir / "live-one.db")

    try:
        response = client.delete("/api/sessions/live-one")
        assert response.status_code == 409
        assert "still recording" in response.json()["detail"]["error"]["message"]
        assert (sessions_dir / "live-one.db").is_file()
    finally:
        manager._store.close()
        manager._store = None


def test_deleting_a_finished_session_removes_it(client: TestClient, sessions_dir) -> None:
    write_session(sessions_dir, "finished-one")

    assert client.delete("/api/sessions/finished-one").json() == {"deleted": True}
    assert not (sessions_dir / "finished-one.db").exists()
    assert client.get("/api/sessions").json()["sessions"] == []


def test_deleting_something_that_is_already_gone_is_not_an_error(client: TestClient) -> None:
    assert client.delete("/api/sessions/never-existed").json() == {"deleted": False}
