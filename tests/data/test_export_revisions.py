"""An export can name its transcription pass (D-066).

D-022 made every export serve the latest pass, never the union, and left one gap it recorded in
the checklist twice: there was no way to ask for the *other* pass. A window session that transcribed
live and again afterwards holds two, the Live/Final switch shows either, and the file always shipped
the newest. These tests are about the parameter that closes that, and about what a session says it
holds so a page can offer the choice only where there is one.
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

LIVE_TEXT = "the live pass heard eigen value problem"
FINAL_TEXT = "the final pass heard the eigenvalue problem"
KEY = "20260101-1200-abc"


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "sessions"
    directory.mkdir()
    return directory


@pytest.fixture
def client(tmp_path: Path, sessions_dir: Path) -> TestClient:
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"storage.session_dir": str(sessions_dir)})
    with TestClient(create_app(config=store)) as client:
        yield client


def segment(identifier: int, text: str, *, revision: int, start: float) -> Segment:
    return Segment(id=identifier, text=text, start=start, end=start + 5.0, revision=revision)


def write_two_pass_session(directory: Path, name: str = KEY) -> Path:
    path = directory / f"{name}.db"
    with TranscriptStore(path, metadata=SessionMetadata(session_id=name, title="Spectra")) as store:
        store.append_segment(segment(1, LIVE_TEXT, revision=0, start=0.0))
        store.append_segment(segment(2, "and then said something else", revision=0, start=5.0))
        store.append_segment(segment(3, FINAL_TEXT, revision=1, start=0.0))
        store.append_segment(segment(4, "and then said something else", revision=1, start=5.0))
    return path


def write_one_pass_session(directory: Path, name: str = KEY) -> Path:
    path = directory / f"{name}.db"
    with TranscriptStore(path, metadata=SessionMetadata(session_id=name, title="Only")) as store:
        store.append_segment(segment(1, "the only pass", revision=0, start=0.0))
    return path


# -- the store ----------------------------------------------------------------------------


def test_no_revision_means_the_latest(tmp_path: Path) -> None:
    with TranscriptStore(write_two_pass_session(tmp_path)) as store:
        assert [s.text for s in store.segments_for(None)][0] == FINAL_TEXT


def test_a_named_revision_is_that_one(tmp_path: Path) -> None:
    with TranscriptStore(write_two_pass_session(tmp_path)) as store:
        assert [s.text for s in store.segments_for(0)][0] == LIVE_TEXT
        assert [s.text for s in store.segments_for(1)][0] == FINAL_TEXT


def test_a_revision_the_session_does_not_hold_is_none_not_empty(tmp_path: Path) -> None:
    """So a route can say 404 rather than serve a file that looks like a transcript of silence."""
    with TranscriptStore(write_two_pass_session(tmp_path)) as store:
        assert store.segments_for(7) is None


def test_an_empty_session_answers_revision_zero_with_nothing_rather_than_refusing(
    tmp_path: Path,
) -> None:
    """A client that has never seen a revision list asks for 0; before the first segment that is
    not a mistake."""
    with TranscriptStore(tmp_path / "empty.db") as store:
        assert store.segments_for(0) == []
        assert store.segments_for(None) == []


# -- a past session's export -------------------------------------------------------------------


def test_an_export_can_ask_for_the_live_pass(client: TestClient, sessions_dir: Path) -> None:
    write_two_pass_session(sessions_dir)
    body = client.get(f"/api/sessions/{KEY}/export", params={"fmt": "markdown", "revision": 0})

    assert body.status_code == 200
    assert LIVE_TEXT in body.text
    assert FINAL_TEXT not in body.text
    assert body.text.count("and then said something else") == 1


def test_an_export_with_no_revision_is_still_the_latest(
    client: TestClient, sessions_dir: Path
) -> None:
    write_two_pass_session(sessions_dir)
    body = client.get(f"/api/sessions/{KEY}/export", params={"fmt": "markdown"}).text

    assert FINAL_TEXT in body
    assert LIVE_TEXT not in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "srt", "vtt", "json"])
def test_every_format_honours_the_revision(
    client: TestClient, sessions_dir: Path, fmt: str
) -> None:
    write_two_pass_session(sessions_dir)
    body = client.get(f"/api/sessions/{KEY}/export", params={"fmt": fmt, "revision": 0}).text

    assert "eigen value" in body
    assert "the eigenvalue" not in body


def test_an_unknown_revision_is_a_404_that_names_what_exists(
    client: TestClient, sessions_dir: Path
) -> None:
    write_two_pass_session(sessions_dir)
    response = client.get(f"/api/sessions/{KEY}/export", params={"fmt": "markdown", "revision": 7})

    assert response.status_code == 404
    error = response.json()["detail"]["error"]
    assert error["code"] == "unknown-revision"
    assert "0, 1" in error["message"]


# -- what a session says it holds ------------------------------------------------------------


def test_the_listing_says_which_passes_a_session_holds(
    client: TestClient, sessions_dir: Path
) -> None:
    """The page draws a version control only where there is a choice."""
    write_two_pass_session(sessions_dir)
    write_one_pass_session(sessions_dir, "20260102-1200-one")
    sessions = {item["key"]: item for item in client.get("/api/sessions").json()["sessions"]}

    assert sessions[KEY]["revisions"] == [0, 1]
    assert sessions["20260102-1200-one"]["revisions"] == [0]


def test_reading_a_session_can_name_a_pass_and_reports_the_rest(
    client: TestClient, sessions_dir: Path
) -> None:
    write_two_pass_session(sessions_dir)

    latest = client.get(f"/api/sessions/{KEY}").json()
    assert latest["revisions"] == [0, 1]
    assert latest["revision"] == 1
    assert latest["segments"][0]["text"] == FINAL_TEXT

    live = client.get(f"/api/sessions/{KEY}", params={"revision": 0}).json()
    assert live["revision"] == 0
    assert live["segments"][0]["text"] == LIVE_TEXT

    assert client.get(f"/api/sessions/{KEY}", params={"revision": 3}).status_code == 404
