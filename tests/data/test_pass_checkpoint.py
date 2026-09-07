"""Where a transcription pass got to, written into the transcript it belongs to (D-045).

D-021 said a pass is deliberately *not* persisted, because re-running is cheap. That reasoning was
about crash recovery and does not survive a user who pressed pause on purpose: a forty-minute
recording is twenty-seven minutes of CPU.

The interesting case here is the **old database**. `archive.py` opens session files this application
wrote months ago, and a table added later does not exist in them. `CREATE TABLE IF NOT EXISTS` in
the schema script covers it, and this is what proves it.
"""

from __future__ import annotations

import sqlite3

import pytest
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.transcript import archive
from app.services.transcript.store import TranscriptStore


@pytest.fixture
def store(tmp_path):
    handle = TranscriptStore(
        tmp_path / "20260101-120000-aaaaaa.db", metadata=SessionMetadata(session_id="a")
    )
    yield handle
    handle.close()


# -- writing and reading a checkpoint -------------------------------------------------------


def test_a_checkpoint_round_trips(store) -> None:
    store.record_pass(
        revision=1,
        source_path="/tmp/audio.wav",
        total_seconds=2400.0,
        state="running",
        next_start_s=360.0,
        last_segment_id=42,
        prompt="operator theory",
    )

    [row] = store.passes()

    assert row["revision"] == 1
    assert row["source_path"] == "/tmp/audio.wav"
    assert row["next_start_s"] == 360.0
    assert row["last_segment_id"] == 42
    assert row["prompt"] == "operator theory"


def test_a_second_checkpoint_replaces_the_first(store) -> None:
    """Written on every window. Appending instead would leave a table with one row per thirty
    seconds of a talk, and nothing would ever read the older ones."""
    for position in (30.0, 60.0, 90.0):
        store.record_pass(
            revision=1,
            source_path="/tmp/a.wav",
            total_seconds=600.0,
            state="running",
            next_start_s=position,
        )

    rows = store.passes()

    assert len(rows) == 1
    assert rows[0]["next_start_s"] == 90.0


def test_two_passes_over_one_session_are_kept_apart(store) -> None:
    """A window session runs a live pass and a post-capture pass, which are revisions 0 and 1
    (D-022). One row each."""
    store.record_pass(revision=0, source_path="/tmp/a.wav", total_seconds=10.0, state="done")
    store.record_pass(revision=1, source_path="/tmp/a.wav", total_seconds=10.0, state="paused")

    assert [row["revision"] for row in store.passes()] == [1, 0]


# -- which pass can be picked up ------------------------------------------------------------


def test_a_held_pass_is_resumable(store) -> None:
    store.record_pass(
        revision=1,
        source_path="/tmp/a.wav",
        total_seconds=600.0,
        state="paused",
        next_start_s=120.0,
    )

    assert store.resumable_pass()["next_start_s"] == 120.0


def test_a_pass_still_marked_running_is_resumable(store) -> None:
    """**The case this table exists for.** A killed process writes no terminal state, because
    nothing got the chance — so "running" with nobody running it is precisely an interruption."""
    store.record_pass(
        revision=1,
        source_path="/tmp/a.wav",
        total_seconds=600.0,
        state="running",
        next_start_s=90.0,
    )

    assert store.resumable_pass() is not None


def test_a_finished_pass_is_not_resumable(store) -> None:
    store.record_pass(revision=1, source_path="/tmp/a.wav", total_seconds=600.0, state="done")

    assert store.resumable_pass() is None


def test_a_cancelled_pass_is_not_offered_again(store) -> None:
    """The user said they did not want it. Offering to resume would be the application arguing."""
    store.record_pass(revision=1, source_path="/tmp/a.wav", total_seconds=600.0, state="cancelled")

    assert store.resumable_pass() is None


def test_a_session_with_no_pass_has_nothing_to_resume(store) -> None:
    assert store.resumable_pass() is None


def test_finishing_a_pass_keeps_the_row(store) -> None:
    """What a pass was, and where it stopped, is a fact about the recording worth keeping."""
    store.record_pass(
        revision=1, source_path="/tmp/a.wav", total_seconds=600.0, state="paused", next_start_s=42.0
    )

    store.finish_pass(1, "cancelled")

    [row] = store.passes()
    assert row["state"] == "cancelled"
    assert row["next_start_s"] == 42.0


# -- the migration --------------------------------------------------------------------------


def test_the_table_appears_in_a_database_written_before_it_existed(tmp_path) -> None:
    """`archive.py` opens session files months old. A table added later does not exist in them,
    and a query against a missing table raises rather than returning nothing — which is how this
    application previously turned a whole directory of sessions into "not readable"."""
    path = tmp_path / "20250101-120000-old000.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE session (id INTEGER PRIMARY KEY, session_id TEXT, started_at TEXT,
                              ended_at TEXT, title TEXT, venue TEXT, speaker TEXT,
                              session_prompt TEXT, config_json TEXT, mode TEXT);
        CREATE TABLE segments (id INTEGER PRIMARY KEY, text TEXT, start REAL, end REAL,
                               wall_clock TEXT, confidence REAL, model_id TEXT, speaker TEXT,
                               words_json TEXT);
        INSERT INTO segments (id, text, start, end) VALUES (1, 'a sentence', 0.0, 2.0);
        """
    )
    old.commit()
    old.close()

    store = TranscriptStore(path)
    try:
        store.record_pass(revision=0, source_path="/tmp/a.wav", total_seconds=5.0, state="paused")

        assert store.resumable_pass() is not None
        assert store.stats().segment_count == 1, "the existing transcript survives the migration"
    finally:
        store.close()


def test_the_listing_still_reads_a_session_that_holds_a_checkpoint(tmp_path) -> None:
    """`archive.describe` opens read-only and does not migrate. A new table must not make an
    otherwise fine session unreadable."""
    from app.config import ConfigStore

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    store = TranscriptStore(
        sessions / "20260101-120000-bbbbbb.db", metadata=SessionMetadata(session_id="b")
    )
    store.append_segment(Segment(id=1, text="a few words", start=0.0, end=2.0))
    store.record_pass(revision=0, source_path="/tmp/a.wav", total_seconds=5.0, state="paused")
    store.close()

    config = ConfigStore(config_path=tmp_path / "c.json")
    config.update({"storage.session_dir": str(sessions)})

    [row] = archive.list_sessions(config.resolve())

    assert row.problem == ""
    assert row.segments == 1
