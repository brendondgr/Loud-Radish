"""Past sessions on disk (BE §17).

The live application only ever knows about the session that is currently open. Everything before
that is a SQLite file in the session directory, and until this module existed there was no way to
reach one from the interface at all — a talk that had been recorded, transcribed, summarised, and
stopped became unreachable the moment it ended.

**What a session holds is answered from the recording folder, not guessed.** A session's transcript
database and its recording directory share a name — ``<YYYYMMDD-HHMMSS>-<session-id>`` — so asking
whether a past session has a video is one directory listing rather than a filename search. That is
what lets the page say "video · audio · transcript" instead of leaving the user to open each one to
find out (and it is what the web-app export tests before it will run).

**Reading here is strictly read-only and defensive.** The directory is a user directory: it will
contain half-written files from a session that crashed, files from an older schema, and whatever
else happens to be sitting there. One unreadable file must not take the listing down with it, so
every failure is reported as a row rather than raised — a session the user can see and cannot open
is far more useful than a page that will not load.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ...config.schema import AppConfig
from ..recording.layout import resolve_recording

logger = logging.getLogger(__name__)

#: Files in the session directory that are ours.
SESSION_SUFFIX = ".db"


@dataclass(frozen=True)
class SessionMedia:
    """What a past session actually holds, so the list can say so without opening anything.

    ``transcript`` is true when the database has segments in it — not merely when the file exists.
    A session that recorded audio and whose transcription pass never ran leaves a database with no
    segments, and calling that "has a transcript" is the answer that sends someone to open it and
    find nothing.
    """

    video: bool = False
    #: Whether there is sound to listen to — from the separate WAV, or muxed into the video.
    #: **Not** "is there a WAV": a successful transcription pass deletes that, so testing for it
    #: reported no audio exactly when the recording was healthiest.
    audio: bool = False
    transcript: bool = False
    #: Whether the untranscribed WAV is still on disk. Narrower than ``audio`` and rarer: it means
    #: a pass has not run, or failed, or audio retention is on.
    audio_file: bool = False
    #: Bytes in the recording folder. Zero when there is no folder.
    recording_bytes: int = 0

    @property
    def exportable(self) -> bool:
        """Whether this session can become a self-contained web application.

        All three, because the export *is* the three of them: a page with a video panel, a
        synchronised transcript, and questions asked against it. Two out of three is a different
        artefact, and offering it under the same name would disappoint quietly.
        """
        return self.video and self.audio and self.transcript

    def as_dict(self) -> dict[str, Any]:
        return {
            "video": self.video,
            "audio": self.audio,
            "transcript": self.transcript,
            "audio_file": self.audio_file,
            "recording_bytes": self.recording_bytes,
            "exportable": self.exportable,
        }


@dataclass(frozen=True)
class ArchivedSession:
    """One past session, described without opening its full transcript."""

    #: Stem of the file, used as the id in URLs. Never a path — that would let a request name any
    #: file on the machine.
    key: str
    path: str
    title: str
    started_at: str
    ended_at: str
    segments: int
    words: int
    duration_seconds: float
    summaries: int
    glossary_terms: int
    #: How many turns the user exchanged with the assistant during this recording. Counted so the
    #: conversation can be offered as its own export only where there is one (D-037).
    chat_messages: int
    size_bytes: int
    #: What this session's recording folder actually holds. See :class:`SessionMedia`.
    media: SessionMedia = field(default_factory=lambda: SessionMedia())
    #: Which transcription passes the session holds (D-022): usually ``[0]``, and ``[0, 1]`` for a
    #: window session that transcribed live and again afterwards. The recordings page offers a
    #: choice of pass to export only when there are two (D-066).
    revisions: list[int] = field(default_factory=list)
    #: Empty when the file is readable. Otherwise says what is wrong with it.
    problem: str = ""

    @property
    def readable(self) -> bool:
        """Whether this session can be opened and exported."""
        return not self.problem

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``GET /api/sessions``."""
        return {
            "key": self.key,
            "title": self.title,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "segments": self.segments,
            "words": self.words,
            "duration_seconds": round(self.duration_seconds, 1),
            "summaries": self.summaries,
            "glossary_terms": self.glossary_terms,
            "chat_messages": self.chat_messages,
            "size_bytes": self.size_bytes,
            "media": self.media.as_dict(),
            "revisions": list(self.revisions),
            "problem": self.problem,
            "readable": self.readable,
        }


def session_dir(config: AppConfig) -> Path:
    """Where sessions are written, as configured."""
    return Path(config.storage.session_dir).expanduser()


def recording_dir(config: AppConfig) -> Path:
    """Where recordings are written, as configured."""
    return Path(config.recording.recording_dir).expanduser()


def list_sessions(config: AppConfig) -> list[ArchivedSession]:
    """Describe every session on disk, newest first."""
    directory = session_dir(config)
    try:
        files = sorted(
            (path for path in directory.iterdir() if path.suffix == SESSION_SUFFIX),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        logger.info("No session directory at %s yet", directory)
        return []

    recordings = recording_dir(config)
    return [describe(path, recordings) for path in files]


def newest_with_segments(config: AppConfig) -> Path | None:
    """The most recent session that actually holds a transcript, or ``None``.

    What the application reopens at start-up so the last talk is still on the page (D-041).

    **Ordered by the timestamp in the filename, not by mtime.** `list_sessions` sorts by mtime
    because that is what a *listing* wants — a file touched recently is one the user was recently
    doing something with. This question is different: it asks which talk happened last, and a
    polish pass, an export or a post-capture transcription all write into a database days after the
    talk it holds ended. The stem is `YYYYMMDD-HHMMSS-<id>`, so sorting it as text sorts it by time.

    **Sessions with no segments are skipped.** Reopening one puts an empty transcript on the page,
    which is indistinguishable from the fault this exists to fix. Unreadable files are skipped for
    the same reason plus a better one: this opens the file for writing afterwards, and a database
    that could not be read is not one to open that way.
    """
    directory = session_dir(config)
    try:
        paths = sorted(
            (path for path in directory.iterdir() if path.suffix == SESSION_SUFFIX),
            key=lambda path: path.stem,
            reverse=True,
        )
    except OSError:
        return None

    for path in paths:
        session = describe(path)
        if session.problem:
            continue
        if session.segments > 0:
            return path
    return None


def describe(path: Path, recordings: Path | None = None) -> ArchivedSession:
    """Read one session file's summary without loading its transcript.

    ``recordings`` is the recordings root. Given one, the returned row also says what media the
    session holds; omitted, it reports none — which is the honest answer when nobody said where to
    look, rather than a guess.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return _unreadable(path, 0, f"Cannot be read ({type(exc).__name__}).")

    try:
        # Read-only URI, so a listing can never modify a session — and so a file mid-write by
        # another process is opened rather than locked against.
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return _unreadable(path, size, f"Could not be opened ({type(exc).__name__}).")

    try:
        # **One transcription pass, not every row.** A session that transcribed live and then ran
        # a post-capture pass holds both (D-022), and counting the table reported roughly double
        # what the session contains — 46 segments and 479 words for a recording of 26. Every other
        # reader takes the latest revision; this one was missed when that rule landed.
        #
        # Guarded on the column existing, because this connection is **read-only and does not
        # migrate**. Databases written before the revision column exist in the wild — 25 of them in
        # the directory this was found in — and a query naming a missing column would turn every
        # one of those rows into "not a readable session file".
        stats = connection.execute(_stats_query(connection)).fetchone()
        meta = connection.execute("SELECT * FROM session WHERE id = 1").fetchone()
        summaries = connection.execute("SELECT COUNT(*) AS n FROM summaries").fetchone()
        glossary = connection.execute("SELECT COUNT(*) AS n FROM glossary").fetchone()
        # **Guarded on the table existing**, for the same reason the revision column is. This
        # connection is read-only and does not migrate, and a count of a table an older database
        # does not have would turn every one of those sessions into "not a readable session file"
        # — which is exactly what it did, caught by the test written for the previous instance.
        chat = _count_or_zero(connection, "chat_messages")
        revisions = _revisions(connection)
    except sqlite3.Error as exc:
        # A file from an older schema, or one truncated by a crash. Listed with the reason.
        return _unreadable(path, size, f"Not a readable session file ({type(exc).__name__}).")
    finally:
        connection.close()

    segments = int(stats["segments"] or 0)
    started = (meta["started_at"] if meta else "") or _mtime(path)
    return ArchivedSession(
        key=path.stem,
        path=str(path),
        title=(meta["title"] if meta else "") or _default_title(started),
        started_at=started,
        ended_at=(meta["ended_at"] if meta else "") or "",
        segments=segments,
        words=int(stats["words"] or 0),
        duration_seconds=float(stats["duration"] or 0.0),
        summaries=int(summaries["n"] or 0),
        glossary_terms=int(glossary["n"] or 0),
        chat_messages=chat,
        size_bytes=size,
        media=media_for(path.stem, recordings, has_transcript=segments > 0),
        revisions=revisions,
    )


def find(config: AppConfig, key: str) -> Path | None:
    """Resolve a session key to a path inside the session directory.

    Matched against the listing rather than joined onto the directory: a key is user input, and
    ``../../etc/passwd`` joined onto a directory is still a path traversal however harmless the
    caller looks.
    """
    directory = session_dir(config).resolve()
    candidate = (directory / f"{key}{SESSION_SUFFIX}").resolve()
    try:
        candidate.relative_to(directory)
    except ValueError:
        logger.warning("Refusing a session key that escapes the session directory: %r", key)
        return None
    return candidate if candidate.is_file() else None


def _count_or_zero(connection: sqlite3.Connection, table: str) -> int:
    """Rows in ``table``, or zero when this database predates it."""
    if not _has_table(connection, table):
        return 0
    row = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()  # noqa: S608
    return int(row["n"] or 0)


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _revisions(connection: sqlite3.Connection) -> list[int]:
    """Every pass this session holds, in order — guarded like the stats query, because this
    connection is read-only and does not migrate. A database from before the column holds one
    pass if it holds any segments at all."""
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(segments)")}
    except sqlite3.Error:
        return []
    if "revision" not in columns:
        row = connection.execute("SELECT COUNT(*) AS n FROM segments").fetchone()
        return [0] if int(row["n"] or 0) > 0 else []
    rows = connection.execute("SELECT DISTINCT revision FROM segments ORDER BY revision")
    return [int(row[0]) for row in rows]


def _stats_query(connection: sqlite3.Connection) -> str:
    """The totals query, narrowed to the latest pass when this database knows about passes."""
    totals = """
        SELECT COUNT(*) AS segments,
               COALESCE(MAX(end), 0) - COALESCE(MIN(start), 0) AS duration,
               COALESCE(SUM(LENGTH(text) - LENGTH(REPLACE(text, ' ', '')) + 1), 0) AS words
        FROM segments
    """
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(segments)")}
    except sqlite3.Error:
        return totals
    if "revision" not in columns:
        return totals
    return f"{totals} WHERE revision = (SELECT COALESCE(MAX(revision), 0) FROM segments)"


def media_for(key: str, recordings: Path | None, *, has_transcript: bool) -> SessionMedia:
    """What the recording folder named ``key`` holds, if there is one.

    Never raises. A recordings directory that has been moved, renamed in settings, or deleted
    entirely must cost the indicators, not the listing — a page that will not load is worse than a
    page that cannot say whether there is a video.
    """
    if recordings is None:
        return SessionMedia(transcript=has_transcript)
    try:
        layout = resolve_recording(recordings, key)
        if layout is None or not layout.exists():
            return SessionMedia(transcript=has_transcript)
        return SessionMedia(
            video=layout.existing_video() is not None,
            audio=layout.has_playable_audio(),
            transcript=has_transcript,
            audio_file=layout.has_audio(),
            recording_bytes=layout.size_bytes(),
        )
    except OSError:
        logger.warning("Could not read the recording folder for %s", key)
        return SessionMedia(transcript=has_transcript)


def _unreadable(path: Path, size: int, problem: str) -> ArchivedSession:
    return ArchivedSession(
        key=path.stem,
        path=str(path),
        title=path.stem,
        started_at=_mtime(path),
        ended_at="",
        segments=0,
        words=0,
        duration_seconds=0.0,
        summaries=0,
        glossary_terms=0,
        chat_messages=0,
        size_bytes=size,
        problem=problem,
    )


def _mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
    except OSError:
        return ""


def _default_title(started: str) -> str:
    """A name for a session nobody titled, which is most of them."""
    try:
        when = datetime.fromisoformat(started)
    except ValueError:
        return "Untitled session"
    return f"Session on {when.strftime('%d %b %Y, %H:%M')}"
