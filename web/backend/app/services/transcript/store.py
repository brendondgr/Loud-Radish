"""The transcript store (BE §8).

A session is an append-only ordered list of segments, written through to disk as they commit. Not
held in memory and flushed at the end: a crash eighty minutes into a ninety-minute talk must lose at
most the last few seconds, and the only way to guarantee that is to have already written the rest.

SQLite because it survives a crash, supports full-text search over the transcript for free, and
needs no server. One file per session, under the configured session directory.

Writes are synchronous and take a lock; the store is called from the ASR worker thread, never from
the event loop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...models.segment import Segment
from ...models.session import (
    ChatMessage,
    GlossaryTerm,
    PolishedBlock,
    SessionMetadata,
    SessionStats,
    Summary,
)
from ..asr.contract import WordToken

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class TranscriptStore:
    """Owns one session's SQLite file."""

    def __init__(self, path: str | Path, metadata: SessionMetadata | None = None) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(str(self._path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._create_schema()
        if metadata is not None:
            self.write_metadata(metadata)

    # -- lifecycle -----------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Where this session is stored."""
        return self._path

    def close(self) -> None:
        """Close the database. Committed data is already durable."""
        with self._lock:
            self._connection.close()

    def __enter__(self) -> TranscriptStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        # **Migration first.** The schema script creates an index on `segments (revision, id)`, and
        # on a database written before that column existed the whole script fails on that one
        # statement — which was found by the test for exactly this case, not by reading it.
        self._migrate()
        with self._lock:
            self._connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._connection.commit()

    def _migrate(self) -> None:
        """Bring an older session file up to the current schema.

        `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so a column added
        later never appears in a database written before it. This matters because
        `services/transcript/archive.py` opens session files this application wrote months ago, and
        an unguarded query against a missing column raises rather than returning nothing.

        A no-op on a fresh database, where there is no `segments` table yet for the script below to
        have missed anything in.
        """
        with self._lock:
            existing = self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'segments'"
            ).fetchone()
            if existing is None:
                return

            columns = {
                row["name"] for row in self._connection.execute("PRAGMA table_info(segments)")
            }
            if "revision" not in columns:
                self._connection.execute(
                    "ALTER TABLE segments ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
                self._connection.commit()
                logger.info("Added the revision column to %s", self._path.name)

    # -- session metadata ----------------------------------------------------------

    def write_metadata(self, metadata: SessionMetadata) -> None:
        """Insert or replace the session row."""
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO session
                    (id, session_id, title, venue, speaker, started_at, ended_at,
                     session_prompt, config_json)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    session_id = excluded.session_id,
                    title = excluded.title,
                    venue = excluded.venue,
                    speaker = excluded.speaker,
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at,
                    session_prompt = excluded.session_prompt,
                    config_json = excluded.config_json
                """,
                (
                    metadata.session_id,
                    metadata.title,
                    metadata.venue,
                    metadata.speaker,
                    metadata.started_at.isoformat(),
                    metadata.ended_at.isoformat() if metadata.ended_at else None,
                    metadata.session_prompt,
                    json.dumps(metadata.config),
                ),
            )
            self._connection.commit()

    def metadata(self) -> SessionMetadata | None:
        """Read the session row, or ``None`` for a store that has never had one written."""
        row = self._query_one("SELECT * FROM session WHERE id = 1")
        if row is None:
            return None
        return SessionMetadata(
            session_id=row["session_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
            title=row["title"],
            venue=row["venue"],
            speaker=row["speaker"],
            session_prompt=row["session_prompt"],
            config=json.loads(row["config_json"] or "{}"),
        )

    def mark_ended(self, when: datetime | None = None) -> None:
        """Record that the session has stopped."""
        with self._lock:
            self._connection.execute(
                "UPDATE session SET ended_at = ? WHERE id = 1",
                ((when or datetime.now(UTC)).isoformat(),),
            )
            self._connection.commit()

    # -- segments ------------------------------------------------------------------

    def append_segment(self, segment: Segment, store_words: bool = True) -> None:
        """Write one committed segment through to disk.

        Called the moment a segment commits, not batched: batching trades the crash guarantee for
        throughput the workload does not need — segments arrive every few seconds, not thousands a
        second.
        """
        self.append_segments([segment], store_words=store_words)

    def append_segments(self, segments: list[Segment], store_words: bool = True) -> None:
        """Write several segments in one transaction."""
        if not segments:
            return
        rows = [
            (
                segment.id,
                segment.text,
                segment.start,
                segment.end,
                segment.wall_clock.isoformat(),
                segment.confidence,
                segment.model_id,
                segment.speaker,
                _encode_words(segment.words) if store_words else None,
                segment.revision,
            )
            for segment in segments
        ]
        with self._lock:
            self._connection.executemany(
                """
                INSERT OR IGNORE INTO segments
                    (id, text, start, end, wall_clock, confidence, model_id, speaker,
                     words_json, revision)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._connection.commit()

    def segment(self, segment_id: int) -> Segment | None:
        """Fetch one segment by id."""
        row = self._query_one("SELECT * FROM segments WHERE id = ?", (segment_id,))
        return _row_to_segment(row) if row else None

    def revisions(self) -> list[int]:
        """Which transcription passes this session holds, ascending (D-022).

        Usually just ``[0]``. A window session that ran both a live pass and a post-capture one
        holds ``[0, 1]``, and the interface offers a Live/Final switch only when it does.
        """
        rows = self._query("SELECT DISTINCT revision FROM segments ORDER BY revision")
        return [int(row["revision"]) for row in rows]

    def latest_revision(self) -> int:
        """The newest pass present, which is what a reader is shown by default."""
        row = self._query_one("SELECT MAX(revision) AS newest FROM segments")
        return int(row["newest"]) if row and row["newest"] is not None else 0

    def all_segments(self) -> list[Segment]:
        """Every segment, in id order — *including* every transcription pass.

        Rarely what a reader wants. Use :meth:`latest_segments` for anything shown or exported; this
        is for the archive, the migration, and anything that genuinely means "every row".
        """
        return [_row_to_segment(row) for row in self._query("SELECT * FROM segments ORDER BY id")]

    def latest_segments(self) -> list[Segment]:
        """The transcript a reader should be shown: the newest pass, and only that one (D-022).

        Not :meth:`all_segments`. A session that transcribed live and again afterwards holds both
        passes over the *same* audio under different ids, so their union is the talk said twice —
        which is what an export and a past-session read were serving. Keeping both on disk is the
        point of revisions; concatenating them was never part of it.
        """
        return self.segments_at(self.latest_revision())

    def segments_at(self, revision: int) -> list[Segment]:
        """Every segment from one transcription pass, in order."""
        return [
            _row_to_segment(row)
            for row in self._query(
                "SELECT * FROM segments WHERE revision = ? ORDER BY id", (revision,)
            )
        ]

    def segments_since(self, segment_id: int, limit: int | None = None) -> list[Segment]:
        """Everything after ``segment_id`` — the reconnection replay path (BE §12.4).

        Ordered by id rather than by time, because ids are what the client tracks and what makes the
        replay idempotent.
        """
        sql = "SELECT * FROM segments WHERE id > ? ORDER BY id"
        params: tuple[Any, ...] = (segment_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (segment_id, limit)
        return [_row_to_segment(row) for row in self._query(sql, params)]

    def segments_in_range(self, start: float, end: float) -> list[Segment]:
        """Every segment overlapping the time range — "summarise the last ten minutes".

        Overlap rather than containment: a segment straddling the boundary is part of what was said
        in that window, and dropping it would lose the sentence the user is asking about.
        """
        return [
            _row_to_segment(row)
            for row in self._query(
                "SELECT * FROM segments WHERE end > ? AND start < ? ORDER BY id", (start, end)
            )
        ]

    def last_segment_id(self) -> int:
        """The highest segment id written, or zero for an empty transcript."""
        row = self._query_one("SELECT MAX(id) AS max_id FROM segments")
        return int(row["max_id"]) if row and row["max_id"] is not None else 0

    def last_segment_end(self) -> float:
        """Where committed transcript currently ends, in session-relative seconds.

        The polish worker polls this every second to decide whether a chunk is ready, so it is a
        single aggregate rather than a scan — asking for the segments themselves on every tick
        would read a minute of rows to answer a question about one number.
        """
        row = self._query_one("SELECT MAX(end) AS max_end FROM segments")
        return float(row["max_end"]) if row and row["max_end"] is not None else 0.0

    # -- search --------------------------------------------------------------------

    def search(self, query: str, limit: int = 50) -> list[Segment]:
        """Full-text search over the transcript.

        Backs both the UI search box and retrieval-style context assembly, so it must stay fast on
        a session with hundreds of segments — which is why it is an FTS5 index rather than a scan.
        """
        cleaned = _fts_query(query)
        if not cleaned:
            return []
        try:
            rows = self._query(
                """
                SELECT segments.* FROM segments_fts
                JOIN segments ON segments.id = segments_fts.rowid
                WHERE segments_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (cleaned, limit),
            )
        except sqlite3.OperationalError as exc:
            # A malformed query is the user's typing, not a bug. Return nothing rather than 500.
            logger.debug("Rejected search query %r: %s", query, exc)
            return []
        return [_row_to_segment(row) for row in rows]

    # -- summaries, glossary, chat -------------------------------------------------

    def add_summary(self, start: float, end: float, text: str) -> Summary:
        """Append one rolling summary and return it with its assigned id."""
        created = datetime.now(UTC)
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO summaries (start, end, text, created_at) VALUES (?, ?, ?, ?)",
                (start, end, text, created.isoformat()),
            )
            self._connection.commit()
            summary_id = int(cursor.lastrowid or 0)
        return Summary(id=summary_id, start=start, end=end, text=text, created_at=created)

    def summaries(self) -> list[Summary]:
        """The running outline, oldest first."""
        return [
            Summary(
                id=row["id"],
                start=row["start"],
                end=row["end"],
                text=row["text"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in self._query("SELECT * FROM summaries ORDER BY start")
        ]

    def add_polished_block(
        self, start: float, end: float, text: str, source_ids: list[int] | None = None
    ) -> PolishedBlock:
        """Append one polished block and return it with its assigned id."""
        created = datetime.now(UTC)
        ids = list(source_ids or [])
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO polished_blocks (start, end, text, source_ids_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (start, end, text, json.dumps(ids), created.isoformat()),
            )
            self._connection.commit()
            block_id = int(cursor.lastrowid or 0)
        return PolishedBlock(
            id=block_id, start=start, end=end, text=text, source_ids=ids, created_at=created
        )

    def polished_blocks(self) -> list[PolishedBlock]:
        """Every polished block, oldest first."""
        return [
            PolishedBlock(
                id=row["id"],
                start=row["start"],
                end=row["end"],
                text=row["text"],
                source_ids=_decode_ids(row["source_ids_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in self._query("SELECT * FROM polished_blocks ORDER BY start, id")
        ]

    def last_polished_end(self) -> float:
        """How far into the talk has been polished, in session-relative seconds.

        Read at session start so a resumed store does not re-polish what it already holds.
        """
        row = self._query_one("SELECT MAX(end) AS max_end FROM polished_blocks")
        return float(row["max_end"]) if row and row["max_end"] is not None else 0.0

    def add_glossary_term(self, term: str, definition: str, first_seen: float) -> GlossaryTerm:
        """Record a term, keeping the earliest first-use timestamp if it is already known."""
        created = datetime.now(UTC)
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO glossary (term, definition, first_seen, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(term) DO UPDATE SET
                    definition = excluded.definition,
                    first_seen = MIN(glossary.first_seen, excluded.first_seen)
                """,
                (term, definition, first_seen, created.isoformat()),
            )
            self._connection.commit()
        return GlossaryTerm(
            term=term, definition=definition, first_seen=first_seen, created_at=created
        )

    def glossary(self) -> list[GlossaryTerm]:
        """Every term, in order of first appearance."""
        return [
            GlossaryTerm(
                term=row["term"],
                definition=row["definition"],
                first_seen=row["first_seen"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in self._query("SELECT * FROM glossary ORDER BY first_seen")
        ]

    def add_chat_message(
        self,
        role: str,
        text: str,
        context_timestamp: float | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ChatMessage:
        """Append one conversation turn."""
        created = datetime.now(UTC)
        with self._lock:
            cursor = self._connection.execute(
                """
                INSERT INTO chat_messages (role, text, created_at, context_timestamp, meta_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (role, text, created.isoformat(), context_timestamp, json.dumps(meta or {})),
            )
            self._connection.commit()
            message_id = int(cursor.lastrowid or 0)
        return ChatMessage(
            id=message_id,
            role=role,  # type: ignore[arg-type]
            text=text,
            created_at=created,
            context_timestamp=context_timestamp,
            meta=meta or {},
        )

    def chat_history(self, limit: int | None = None) -> list[ChatMessage]:
        """The conversation, oldest first."""
        rows = self._query("SELECT * FROM chat_messages ORDER BY id")
        messages = [
            ChatMessage(
                id=row["id"],
                role=row["role"],
                text=row["text"],
                created_at=datetime.fromisoformat(row["created_at"]),
                context_timestamp=row["context_timestamp"],
                meta=json.loads(row["meta_json"] or "{}"),
            )
            for row in rows
        ]
        return messages[-limit:] if limit else messages

    def clear_chat_history(self) -> None:
        """Delete the conversation. The transcript is untouched."""
        with self._lock:
            self._connection.execute("DELETE FROM chat_messages")
            self._connection.commit()

    # -- totals --------------------------------------------------------------------

    def stats(self) -> SessionStats:
        """Totals for the status display and for token budgeting."""
        row = self._query_one(
            """
            SELECT COUNT(*) AS segments,
                   COALESCE(MAX(end), 0) - COALESCE(MIN(start), 0) AS duration,
                   COALESCE(SUM(LENGTH(text) - LENGTH(REPLACE(text, ' ', '')) + 1), 0) AS words
            FROM segments
            """
        )
        summaries = self._query_one("SELECT COUNT(*) AS n FROM summaries")
        glossary = self._query_one("SELECT COUNT(*) AS n FROM glossary")
        polished = self._query_one("SELECT COUNT(*) AS n FROM polished_blocks")

        return SessionStats(
            segment_count=int(row["segments"]) if row else 0,
            word_count=int(row["words"]) if row else 0,
            duration_seconds=float(row["duration"]) if row else 0.0,
            summary_count=int(summaries["n"]) if summaries else 0,
            glossary_count=int(glossary["n"]) if glossary else 0,
            polished_count=int(polished["n"]) if polished else 0,
        )

    # -- internals -----------------------------------------------------------------

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, params).fetchall())

    def _query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, params).fetchone()


def _encode_words(words: list[WordToken]) -> str | None:
    """Serialise word-level detail, or ``None`` when there is none."""
    if not words:
        return None
    return json.dumps(
        [
            {
                "t": word.text,
                "s": round(word.start, 3),
                "e": round(word.end, 3),
                "c": word.confidence,
            }
            for word in words
        ]
    )


def _decode_words(raw: str | None) -> list[WordToken]:
    """Rebuild word-level detail, tolerating a row written by an older schema."""
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [
        WordToken(text=entry["t"], start=entry["s"], end=entry["e"], confidence=entry.get("c"))
        for entry in entries
    ]


def _decode_ids(raw: str | None) -> list[int]:
    """Read a JSON array of segment ids, tolerating a row written by an older schema."""
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [int(entry) for entry in entries] if isinstance(entries, list) else []


def _row_to_segment(row: sqlite3.Row) -> Segment:
    """Build a segment from a database row."""
    return Segment(
        id=row["id"],
        text=row["text"],
        start=row["start"],
        end=row["end"],
        wall_clock=datetime.fromisoformat(row["wall_clock"]),
        words=_decode_words(row["words_json"]),
        confidence=row["confidence"],
        model_id=row["model_id"],
        speaker=row["speaker"],
        # `keys()` rather than a bare lookup: `archive.py` opens session files written before this
        # column existed, and sqlite3.Row raises IndexError on a name it does not have.
        revision=row["revision"] if "revision" in row.keys() else 0,
    )


def _fts_query(query: str) -> str:
    """Turn user typing into a safe FTS5 query.

    Every term is quoted so that punctuation the user typed — a hyphen, a colon, a stray quote —
    is searched for rather than interpreted as FTS syntax and raising.
    """
    terms = [term.strip('"') for term in query.split() if term.strip('"')]
    return " ".join(f'"{term}"' for term in terms)
