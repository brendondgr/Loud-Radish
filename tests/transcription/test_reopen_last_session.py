"""The last finished transcript survives a restart (D-041).

D-031 keeps a finished session's store open until the next session starts, so the page and the
assistant can still read the talk that just ended. It holds it in an attribute, though, and a
process boundary ends that: the database is still on disk, nothing reopens it, and the main page
comes up empty while the Recordings page shows the talk perfectly well.

What is asserted here is mostly which file gets chosen, because that is where this can go wrong
quietly — picking an empty one restores an empty page, which looks exactly like the bug.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from app.config import ConfigStore
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.session import SessionManager
from app.services.transcript import archive
from app.services.transcript.store import TranscriptStore


@pytest.fixture
def sessions_dir(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir()
    return directory


def write_session(directory, key: str, *, segments: int = 3, text: str = "a sentence of words"):
    path = directory / f"{key}.db"
    with TranscriptStore(path, metadata=SessionMetadata(session_id=key)) as store:
        for index in range(segments):
            store.append_segment(
                Segment(id=index + 1, text=text, start=index * 10.0, end=index * 10.0 + 9.0)
            )
    return path


def config_for(tmp_path, sessions_dir, **overrides):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"storage.session_dir": str(sessions_dir), **overrides})
    return store


def manager_for(tmp_path, sessions_dir, **overrides):
    return SessionManager(config_for(tmp_path, sessions_dir, **overrides), emit=lambda *_a: None)


# -- choosing the right one ----------------------------------------------------------------


def test_the_newest_transcript_is_reopened(tmp_path, sessions_dir) -> None:
    write_session(sessions_dir, "20260101-120000-aaaaaa", text="the older talk")
    write_session(sessions_dir, "20260305-093000-bbbbbb", text="the newer talk")

    manager = manager_for(tmp_path, sessions_dir)
    assert manager.reopen_last_session() is True

    assert manager.store is not None
    assert "the newer talk" in manager.store.latest_segments()[0].text


def test_an_empty_session_is_skipped_for_the_newest_one_holding_words(
    tmp_path, sessions_dir
) -> None:
    """The newest file is not always the newest *talk*. A pass that never ran leaves a database
    with no segments, and reopening it restores an empty page — the very fault this fixes."""
    write_session(sessions_dir, "20260101-120000-aaaaaa", text="the real talk")
    write_session(sessions_dir, "20260305-093000-bbbbbb", segments=0)

    manager = manager_for(tmp_path, sessions_dir)
    assert manager.reopen_last_session() is True

    assert "the real talk" in manager.store.latest_segments()[0].text


def test_the_choice_ignores_modification_time(tmp_path, sessions_dir) -> None:
    """A polish pass, an export or a post-capture transcription all write into a database days
    after its talk ended, so mtime says which file was touched last, not which talk
    happened last."""
    older = write_session(sessions_dir, "20260101-120000-aaaaaa", text="january")
    write_session(sessions_dir, "20260305-093000-bbbbbb", text="march")
    # Touch the January file so it is the newest by mtime and the oldest by name.
    now = datetime.now(UTC).timestamp()
    os.utime(older, (now, now))

    manager = manager_for(tmp_path, sessions_dir)
    manager.reopen_last_session()

    assert "march" in manager.store.latest_segments()[0].text


def test_an_unreadable_file_does_not_stop_the_search(tmp_path, sessions_dir) -> None:
    write_session(sessions_dir, "20260101-120000-aaaaaa", text="the readable one")
    (sessions_dir / "20260305-093000-bbbbbb.db").write_bytes(b"not a database at all")

    manager = manager_for(tmp_path, sessions_dir)
    assert manager.reopen_last_session() is True

    assert "the readable one" in manager.store.latest_segments()[0].text


# -- when it should do nothing -------------------------------------------------------------


def test_nothing_is_reopened_when_nothing_has_been_recorded(tmp_path, sessions_dir) -> None:
    manager = manager_for(tmp_path, sessions_dir)

    assert manager.reopen_last_session() is False
    assert manager.store is None


def test_a_missing_session_directory_is_not_a_startup_failure(tmp_path) -> None:
    manager = manager_for(tmp_path, tmp_path / "nowhere")

    assert manager.reopen_last_session() is False
    assert manager.store is None


def test_the_setting_turns_it_off(tmp_path, sessions_dir) -> None:
    write_session(sessions_dir, "20260101-120000-aaaaaa")

    manager = manager_for(tmp_path, sessions_dir, **{"storage.reopen_last_session": False})

    assert manager.reopen_last_session() is False
    assert manager.store is None


def test_a_directory_of_only_empty_sessions_reopens_nothing(tmp_path, sessions_dir) -> None:
    write_session(sessions_dir, "20260101-120000-aaaaaa", segments=0)
    write_session(sessions_dir, "20260305-093000-bbbbbb", segments=0)

    manager = manager_for(tmp_path, sessions_dir)

    assert manager.reopen_last_session() is False
    assert manager.store is None


# -- what the reopened store is good for ---------------------------------------------------


def test_the_reopened_transcript_reports_its_stats(tmp_path, sessions_dir) -> None:
    """`state()` reads through the same `store` property, so a reload has something to re-fetch —
    which is the half of D-031 that was broken separately."""
    write_session(sessions_dir, "20260101-120000-aaaaaa", segments=5)

    manager = manager_for(tmp_path, sessions_dir)
    manager.reopen_last_session()

    assert manager.state()["stats"]["segments"] == 5
    assert manager.is_running is False, "a reopened transcript is readable, not recording"


def test_the_clock_is_positioned_on_the_reopened_talk(tmp_path, sessions_dir) -> None:
    """`session_seconds` is what "the last ten minutes" resolves against. Zero would select
    nothing, which is the fault D-031 found one line below where it promised the opposite."""
    write_session(sessions_dir, "20260101-120000-aaaaaa", segments=5)

    manager = manager_for(tmp_path, sessions_dir)
    manager.reopen_last_session()

    assert manager.session_seconds > 0.0


def test_reopening_never_displaces_a_running_session(tmp_path, sessions_dir) -> None:
    """Called once from the lifespan, but a method that would drop a live session's store if it
    were ever called twice is one line away from doing so."""
    write_session(sessions_dir, "20260101-120000-aaaaaa", text="the archived talk")
    manager = manager_for(tmp_path, sessions_dir)
    live = TranscriptStore(sessions_dir / "20260401-000000-cccccc.db")
    manager._store = live

    assert manager.reopen_last_session() is False
    assert manager.store is live
    live.close()


def test_reopening_twice_holds_exactly_one(tmp_path, sessions_dir) -> None:
    write_session(sessions_dir, "20260101-120000-aaaaaa")
    manager = manager_for(tmp_path, sessions_dir)

    assert manager.reopen_last_session() is True
    first = manager.store
    assert manager.reopen_last_session() is False
    assert manager.store is first


# -- the helper the choice lives in --------------------------------------------------------


def test_the_archive_helper_names_the_file_rather_than_opening_it(tmp_path, sessions_dir) -> None:
    write_session(sessions_dir, "20260101-120000-aaaaaa")
    newest = write_session(sessions_dir, "20260305-093000-bbbbbb")
    config = config_for(tmp_path, sessions_dir).resolve()

    assert archive.newest_with_segments(config) == newest


def test_the_archive_helper_returns_none_on_an_empty_directory(tmp_path, sessions_dir) -> None:
    config = config_for(tmp_path, sessions_dir).resolve()

    assert archive.newest_with_segments(config) is None


def test_a_stale_file_is_still_the_newest_talk(tmp_path, sessions_dir) -> None:
    """Nothing expires. A machine that has not recorded for a month should still show that month-old
    talk rather than nothing, because "the last thing you recorded" is what the page is for."""
    long_ago = (datetime.now(UTC) - timedelta(days=90)).strftime("%Y%m%d-%H%M%S")
    write_session(sessions_dir, f"{long_ago}-aaaaaa", text="a talk from a while back")

    manager = manager_for(tmp_path, sessions_dir)

    assert manager.reopen_last_session() is True
    assert "a while back" in manager.store.latest_segments()[0].text
