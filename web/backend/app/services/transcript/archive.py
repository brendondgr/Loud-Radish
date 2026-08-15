"""Past sessions on disk (BE §17).

The live application only ever knows about the session that is currently open. Everything before
that is a SQLite file in the session directory, and until this module existed there was no way to
reach one from the interface at all — a talk that had been recorded, transcribed, summarised, and
stopped became unreachable the moment it ended.

**Reading here is strictly read-only and defensive.** The directory is a user directory: it will
contain half-written files from a session that crashed, files from an older schema, and whatever
else happens to be sitting there. One unreadable file must not take the listing down with it, so
every failure is reported as a row rather than raised — a session the user can see and cannot open
is far more useful than a page that will not load.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ...config.schema import AppConfig

logger = logging.getLogger(__name__)

#: Files in the session directory that are ours.
SESSION_SUFFIX = ".db"


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
    size_bytes: int
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
            "size_bytes": self.size_bytes,
            "problem": self.problem,
            "readable": self.readable,
        }


def session_dir(config: AppConfig) -> Path:
    """Where sessions are written, as configured."""
    return Path(config.storage.session_dir).expanduser()


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

    return [describe(path) for path in files]


def describe(path: Path) -> ArchivedSession:
    """Read one session file's summary without loading its transcript."""
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
        stats = connection.execute(
            """
            SELECT COUNT(*) AS segments,
                   COALESCE(MAX(end), 0) - COALESCE(MIN(start), 0) AS duration,
                   COALESCE(SUM(LENGTH(text) - LENGTH(REPLACE(text, ' ', '')) + 1), 0) AS words
            FROM segments
            """
        ).fetchone()
        meta = connection.execute("SELECT * FROM session WHERE id = 1").fetchone()
        summaries = connection.execute("SELECT COUNT(*) AS n FROM summaries").fetchone()
        glossary = connection.execute("SELECT COUNT(*) AS n FROM glossary").fetchone()
    except sqlite3.Error as exc:
        # A file from an older schema, or one truncated by a crash. Listed with the reason.
        return _unreadable(path, size, f"Not a readable session file ({type(exc).__name__}).")
    finally:
        connection.close()

    started = (meta["started_at"] if meta else "") or _mtime(path)
    return ArchivedSession(
        key=path.stem,
        path=str(path),
        title=(meta["title"] if meta else "") or _default_title(started),
        started_at=started,
        ended_at=(meta["ended_at"] if meta else "") or "",
        segments=int(stats["segments"] or 0),
        words=int(stats["words"] or 0),
        duration_seconds=float(stats["duration"] or 0.0),
        summaries=int(summaries["n"] or 0),
        glossary_terms=int(glossary["n"] or 0),
        size_bytes=size,
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
