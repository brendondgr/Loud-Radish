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
    """Deleting the file out from under an open connection loses the talk in progress.

    A running session is **a store plus live metadata**, not a store alone. It used to be faked
    here with the store by itself, which is the very conflation that made every *finished* session
    report itself as recording once the store began outliving its session (D-031).
    """
    write_session(sessions_dir, "live-one")
    manager = client.app.state.session_manager
    manager._store = TranscriptStore(sessions_dir / "live-one.db")
    manager._metadata = SessionMetadata(session_id="live-one")

    try:
        response = client.delete("/api/sessions/live-one")
        assert response.status_code == 409
        assert "still recording" in response.json()["detail"]["error"]["message"]
        assert (sessions_dir / "live-one.db").is_file()
    finally:
        manager._store.close()
        manager._store = None
        manager._metadata = None


def test_a_retained_store_alone_does_not_make_a_session_undeletable(
    client: TestClient, sessions_dir
) -> None:
    """The finished half of the pair above: readable is not the same as being written."""
    write_session(sessions_dir, "finished-but-retained")
    manager = client.app.state.session_manager
    manager._store = TranscriptStore(sessions_dir / "finished-but-retained.db")

    try:
        assert client.get("/api/sessions").json()["running_key"] == ""
        assert client.delete("/api/sessions/finished-but-retained").json() == {"deleted": True}
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


# -- what a session holds ---------------------------------------------------------------------


KEY = "20260829-174113-d60b37a9e3c4"


def _config(tmp_path, sessions_dir, recordings_dir):
    config = ConfigStore(config_path=tmp_path / "media.json")
    config.update(
        {
            "storage.session_dir": str(sessions_dir),
            "recording.recording_dir": str(recordings_dir),
        }
    )
    return config.resolve()


def test_a_session_with_everything_reports_everything(sessions_dir, tmp_path) -> None:
    recordings = tmp_path / "recordings"
    (recordings / KEY).mkdir(parents=True)
    (recordings / KEY / "video.webm").write_bytes(b"a video")
    (recordings / KEY / "audio.wav").write_bytes(b"\x00" * 200)
    write_session(sessions_dir, KEY, segments=3)

    media = archive.list_sessions(_config(tmp_path, sessions_dir, recordings))[0].media

    assert (media.video, media.audio, media.transcript) == (True, True, True)
    assert media.exportable is True
    assert media.recording_bytes > 0


def test_a_session_with_no_recording_folder_reports_only_its_transcript(
    sessions_dir, tmp_path
) -> None:
    """The `live` mode case: there is a transcript and there was never a file."""
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    write_session(sessions_dir, KEY, segments=2)

    media = archive.list_sessions(_config(tmp_path, sessions_dir, recordings))[0].media

    assert (media.video, media.audio, media.transcript) == (False, False, True)
    assert media.exportable is False


def test_an_empty_database_is_not_reported_as_having_a_transcript(sessions_dir, tmp_path) -> None:
    """A pass that never ran leaves a database with no segments. That is not a transcript."""
    recordings = tmp_path / "recordings"
    (recordings / KEY).mkdir(parents=True)
    (recordings / KEY / "audio.wav").write_bytes(b"\x00" * 200)
    write_session(sessions_dir, KEY, segments=0)

    media = archive.list_sessions(_config(tmp_path, sessions_dir, recordings))[0].media

    assert media.transcript is False
    assert media.audio is True
    assert media.exportable is False


def test_audio_deleted_after_a_successful_pass_still_counts_as_audio(
    sessions_dir, tmp_path
) -> None:
    """Retention is off by default, so this is the *normal* end state of a window recording.

    The WAV is gone and the sound is in the video. Reporting "no audio" here was the reported
    fault: it described the healthiest possible outcome as a missing piece.
    """
    recordings = tmp_path / "recordings"
    (recordings / KEY).mkdir(parents=True)
    (recordings / KEY / "video-with-audio.webm").write_bytes(b"a muxed video")
    write_session(sessions_dir, KEY, segments=5)

    media = archive.list_sessions(_config(tmp_path, sessions_dir, recordings))[0].media

    assert (media.video, media.audio, media.transcript) == (True, True, True)
    assert media.audio_file is False


def test_a_missing_recordings_directory_costs_the_indicators_not_the_listing(
    sessions_dir, tmp_path
) -> None:
    write_session(sessions_dir, KEY, segments=1)

    sessions = archive.list_sessions(_config(tmp_path, sessions_dir, tmp_path / "gone"))

    assert len(sessions) == 1
    assert sessions[0].media.video is False


def test_the_media_block_reaches_the_api(client, sessions_dir) -> None:
    write_session(sessions_dir, KEY, segments=2)

    row = client.get("/api/sessions").json()["sessions"][0]

    assert row["media"] == {
        "video": False,
        "audio": False,
        "transcript": True,
        "audio_file": False,
        "recording_bytes": 0,
        "exportable": False,
    }


# -- which row is being written right now ------------------------------------------------------


def test_a_stopped_session_does_not_report_itself_as_recording(client, sessions_dir) -> None:
    """The badge, and the disabled Delete button, both follow this one field.

    `SessionManager.store` keeps returning the last finished session's store on purpose (D-031), so
    a `running_key` derived from it marked every completed recording as still recording — which is
    what a user sees as "it still says recording now even though I finished".
    """
    write_session(sessions_dir, KEY, segments=3)

    started = client.post("/api/session/start", json={})
    if started.status_code != 200:
        pytest.skip("no capture source available in this environment")

    while_running = client.get("/api/sessions").json()["running_key"]
    client.post("/api/session/stop")
    after_stop = client.get("/api/sessions").json()["running_key"]

    assert while_running, "a running session must be marked, or the badge means nothing"
    assert after_stop == ""


def test_a_finished_session_can_be_deleted(client, sessions_dir) -> None:
    """The same fault seen from the other side: the row's Delete button was refused."""
    write_session(sessions_dir, KEY, segments=1)

    started = client.post("/api/session/start", json={})
    if started.status_code != 200:
        pytest.skip("no capture source available in this environment")
    client.post("/api/session/stop")

    running = client.get("/api/sessions").json()["running_key"]
    key = next(s["key"] for s in client.get("/api/sessions").json()["sessions"] if s["key"] != running)

    assert client.delete(f"/api/sessions/{key}").status_code == 200


# -- describing a healthy finished recording ---------------------------------------------------


def test_a_transcribed_window_recording_reports_audio_and_is_exportable(
    sessions_dir, tmp_path
) -> None:
    """The exact shape of the reported recording: video with sound, transcript, no WAV.

    This is what a *successful* window session leaves behind — the pass deleted its own audio and
    the sound is in the muxed video. It was reported as having no audio, which also made it
    non-exportable, so the one recording that had everything was offered the least.
    """
    recordings = tmp_path / "recordings"
    (recordings / KEY).mkdir(parents=True)
    (recordings / KEY / "video-with-audio.webm").write_bytes(b"picture and sound")
    (recordings / KEY / "audio.json").write_text("{}")
    write_session(sessions_dir, KEY, segments=6)

    media = archive.list_sessions(_config(tmp_path, sessions_dir, recordings))[0].media

    assert (media.video, media.audio, media.transcript) == (True, True, True)
    assert media.exportable is True
    # Still reported separately, because "a pass has not run" is a different fact worth having.
    assert media.audio_file is False


def test_the_counts_describe_one_pass_not_two(sessions_dir, tmp_path) -> None:
    """A session with a live pass and a post-capture pass holds the same talk twice (D-022).

    Reported as 46 segments and 479 words for a recording that holds 26 — the listing was the last
    reader still counting the union.
    """
    path = sessions_dir / f"{KEY}.db"
    metadata = SessionMetadata(session_id="d60b37a9e3c4")
    with TranscriptStore(path, metadata=metadata) as store:
        for index in range(4):
            store.append_segment(
                Segment(id=index + 1, text="live text here", start=index, end=index + 1, revision=0)
            )
        for index in range(6):
            store.append_segment(
                Segment(
                    id=index + 100,
                    text="the final pass text",
                    start=index,
                    end=index + 1,
                    revision=1,
                )
            )

    row = archive.list_sessions(_config(tmp_path, sessions_dir, tmp_path / "recordings"))[0]

    assert row.segments == 6
    assert row.words == 6 * 4


def test_a_database_without_a_revision_column_is_still_described(sessions_dir, tmp_path) -> None:
    """The listing connection is read-only and does not migrate.

    Databases written before the revision column exist in the wild — 25 of them in the directory
    this was found in — and a query naming a missing column would turn every one of those rows into
    "not a readable session file".
    """
    _write_legacy_session(sessions_dir / f"{KEY}.db", segments=3)

    row = archive.list_sessions(_config(tmp_path, sessions_dir, tmp_path / "recordings"))[0]

    assert row.problem == ""
    assert row.segments == 3


def _write_legacy_session(path, *, segments: int) -> None:
    """A session file from before the revision column, written by hand.

    Built rather than migrated-backwards: SQLite refuses to drop the column while the index and
    triggers reference it, and a hand-built table is a truer stand-in for a file this old anyway.
    """
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE session (
            id INTEGER PRIMARY KEY CHECK (id = 1), session_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '', venue TEXT NOT NULL DEFAULT '',
            speaker TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, ended_at TEXT,
            session_prompt TEXT NOT NULL DEFAULT '', config_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE segments (
            id INTEGER PRIMARY KEY, text TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
            wall_clock TEXT NOT NULL, confidence REAL, model_id TEXT NOT NULL DEFAULT '',
            speaker TEXT, words_json TEXT
        );
        CREATE TABLE summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, start REAL NOT NULL, end REAL NOT NULL,
            text TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE glossary (
            term TEXT PRIMARY KEY, definition TEXT NOT NULL, first_seen REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO session (id, session_id, started_at) VALUES (1, 'legacy', '2026-01-01T00:00:00')"
    )
    for index in range(segments):
        connection.execute(
            "INSERT INTO segments (id, text, start, end, wall_clock) VALUES (?, ?, ?, ?, ?)",
            (index + 1, "some words here", float(index), float(index) + 1.0, "2026-01-01T00:00:00"),
        )
    connection.commit()
    connection.close()
