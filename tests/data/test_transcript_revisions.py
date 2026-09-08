"""Two transcription passes over one session, both kept (D-022).

A window session can transcribe live *and* again afterwards, and the two disagree — the second saw
the whole talk at once and is usually better. Overwriting the first is the only option that destroys
information, so it is the one not taken: the live transcript is what the user watched and what any
chat citation points into, and replacing it would invalidate a conversation that already happened.

The migration matters as much as the feature. `CREATE TABLE IF NOT EXISTS` does nothing to a table
that already exists, so the column never appears in a database written before it — and
`services/transcript/archive.py` opens exactly those.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.models.segment import Segment
from app.services.transcript import TranscriptStore


def segment(identifier: int, text: str, *, revision: int = 0, start: float = 0.0) -> Segment:
    return Segment(
        id=identifier,
        text=text,
        start=start,
        end=start + 2.0,
        wall_clock=datetime.now(UTC),
        model_id="test",
        revision=revision,
    )


def test_a_session_can_hold_two_passes(tmp_path: Path) -> None:
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "live text", revision=0))
        store.append_segment(segment(2, "final text", revision=1))

        assert store.revisions() == [0, 1]
        assert store.latest_revision() == 1


def test_each_pass_reads_back_on_its_own(tmp_path: Path) -> None:
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "live one", revision=0))
        store.append_segment(segment(2, "live two", revision=0))
        store.append_segment(segment(3, "final one", revision=1))

        assert [s.text for s in store.segments_at(0)] == ["live one", "live two"]
        assert [s.text for s in store.segments_at(1)] == ["final one"]


def test_the_live_pass_survives_the_second(tmp_path: Path) -> None:
    """The property the whole design exists for."""
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "what the user watched", revision=0))
        store.append_segment(segment(2, "the better version", revision=1))

        assert store.segments_at(0), "the live transcript was lost when the second pass ran"


def test_a_session_with_one_pass_reports_one(tmp_path: Path) -> None:
    """No switch is offered, because there is nothing to switch between."""
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "only pass"))
        assert store.revisions() == [0]
        assert store.latest_revision() == 0


def test_an_empty_session_has_no_revisions(tmp_path: Path) -> None:
    with TranscriptStore(tmp_path / "s.db") as store:
        assert store.revisions() == []
        assert store.latest_revision() == 0


def test_the_revision_reaches_the_transport_event(tmp_path: Path) -> None:
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "final", revision=1))
        assert store.segments_at(1)[0].as_event()["revision"] == 1


def test_segments_default_to_the_live_revision(tmp_path: Path) -> None:
    """Every existing caller writes revision 0 without knowing the field exists."""
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(Segment(id=1, text="x", start=0.0, end=1.0))
        assert store.segments_at(0)[0].revision == 0


def test_a_range_read_serves_the_latest_pass_only(tmp_path: Path) -> None:
    """The context, polish and chat paths read through this, and a session holding two passes
    covers the same minutes twice — so an unscoped read said everything twice (D-065)."""
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "live one", revision=0, start=0.0))
        store.append_segment(segment(2, "live two", revision=0, start=2.0))
        store.append_segment(segment(3, "final one", revision=1, start=0.0))
        store.append_segment(segment(4, "final two", revision=1, start=2.0))

        assert [s.text for s in store.segments_in_range(0.0, 10.0)] == ["final one", "final two"]


def test_a_range_read_can_name_the_other_pass(tmp_path: Path) -> None:
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "live one", revision=0, start=0.0))
        store.append_segment(segment(2, "final one", revision=1, start=0.0))

        assert [s.text for s in store.segments_in_range(0.0, 10.0, revision=0)] == ["live one"]


def test_a_range_read_over_one_pass_is_unchanged(tmp_path: Path) -> None:
    """Every ordinary session holds one pass, and this must read exactly as it always has."""
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "first", start=0.0))
        store.append_segment(segment(2, "second", start=2.0))
        store.append_segment(segment(3, "third", start=4.0))

        assert [s.text for s in store.segments_in_range(1.0, 3.0)] == ["first", "second"]
        assert store.segments_in_range(50.0, 60.0) == []


def test_search_spans_both_passes(tmp_path: Path) -> None:
    """FTS indexes rows, not revisions. A hit in either is a hit."""
    with TranscriptStore(tmp_path / "s.db") as store:
        store.append_segment(segment(1, "the eigenvalue problem", revision=0))
        store.append_segment(segment(2, "the eigenvalue problem, restated", revision=1))
        assert len(store.search("eigenvalue")) == 2


# -- the migration ------------------------------------------------------------------------


def build_old_database(path: Path) -> None:
    """A session file exactly as this application wrote them before revisions existed."""
    connection = sqlite3.connect(str(path))
    connection.executescript(
        """
        CREATE TABLE session (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            session_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
            venue TEXT NOT NULL DEFAULT '', speaker TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL, ended_at TEXT,
            session_prompt TEXT NOT NULL DEFAULT '', config_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE segments (
            id INTEGER PRIMARY KEY, text TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
            wall_clock TEXT NOT NULL, confidence REAL, model_id TEXT NOT NULL DEFAULT '',
            speaker TEXT, words_json TEXT
        );
        INSERT INTO session (id, session_id, started_at)
            VALUES (1, 'old0001', '2026-01-01T00:00:00+00:00');
        INSERT INTO segments (id, text, start, end, wall_clock)
            VALUES (1, 'said before revisions existed', 0.0, 2.0, '2026-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()


def test_a_database_written_before_the_column_still_opens(tmp_path: Path) -> None:
    """`archive.py` opens exactly these, and an unguarded query would raise rather than return."""
    path = tmp_path / "old.db"
    build_old_database(path)

    with TranscriptStore(path) as store:
        segments = store.all_segments()
        assert len(segments) == 1
        assert segments[0].text == "said before revisions existed"


def test_older_segments_read_as_the_live_pass(tmp_path: Path) -> None:
    """They were, and defaulting them to anything else would hide them from the default view."""
    path = tmp_path / "old.db"
    build_old_database(path)

    with TranscriptStore(path) as store:
        assert store.all_segments()[0].revision == 0
        assert store.revisions() == [0]


def test_the_migration_is_idempotent(tmp_path: Path) -> None:
    """Opening a session file twice is ordinary — the archive does it on every listing."""
    path = tmp_path / "old.db"
    build_old_database(path)

    for _ in range(3):
        with TranscriptStore(path) as store:
            assert store.revisions() == [0]


def test_a_migrated_database_accepts_a_second_pass(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    build_old_database(path)

    with TranscriptStore(path) as store:
        store.append_segment(segment(2, "the better version", revision=1))
        assert store.revisions() == [0, 1]
