#!/usr/bin/env python3
"""Remove session databases that hold nothing, and refuse to touch the ones that hold something.

Before `tests/conftest.py` isolated the suite from `data/` (Part 3i of the checklist), every test
run wrote real session databases into the developer's own session directory. They are harmless, but
`GET /api/sessions` lists that directory newest first, so the past-recordings page opens on a wall
of empty rows. The cause is long fixed; this clears what it left.

    uv run scripts/prune_empty_sessions.py                 # report only — the default
    uv run scripts/prune_empty_sessions.py --apply         # actually delete
    uv run scripts/prune_empty_sessions.py --before 2026-09-01

**A database with no segments is not automatically a leaving.** A recording whose transcription
failed leaves exactly the same thing — an empty database — next to a folder holding the only copy of
the talk, and deleting that is deleting the audio's one label. Measured in the directory this was
written for: 706 of 968 databases held no segments, and 28 of those owned a recording folder. So a
file is a candidate only when **all** of these hold:

* it opens, and reports zero segments at every revision;
* its recording folder is absent, or exists and is completely empty;
* it has not been written to recently, so a session running right now is never a candidate.

Everything else is listed under what is being kept, with the reason. Nothing outside the session
directory is ever touched: recording folders are reported and left alone, because a folder with
media in it is the evidence, not the index.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web" / "backend"))

from app.config import ConfigStore  # noqa: E402
from app.services.transcript import archive  # noqa: E402

#: A database written more recently than this is assumed to belong to a session in progress. The
#: application holds its SQLite file open for the whole of a recording and writes on every commit,
#: so "modified in the last few minutes" is the cheapest reliable way to spot one from outside the
#: process — and being wrong here means deleting the talk currently being recorded.
LIVE_WINDOW = timedelta(minutes=10)


@dataclass(frozen=True)
class Verdict:
    """One session file, and whether it may go."""

    session: archive.ArchivedSession
    #: Empty when the file is a candidate. Otherwise says why it is being kept.
    keep_because: str

    @property
    def prunable(self) -> bool:
        return not self.keep_because


def judge(session: archive.ArchivedSession, *, now: datetime, before: datetime | None) -> Verdict:
    """Decide whether one session file is a leaving. Reasons are the user-facing text."""
    if session.problem:
        return Verdict(session, "cannot be read, so its contents are unknown")
    if session.segments > 0:
        return Verdict(session, f"holds a transcript ({session.segments} segments)")

    media = session.media
    if media.video or media.audio_file or media.recording_bytes > 0:
        return Verdict(session, f"owns a recording ({media.recording_bytes / 1e6:.1f} MB)")

    modified = _modified(Path(session.path))
    if modified is not None and now - modified < LIVE_WINDOW:
        return Verdict(session, "was written to just now, so a session may be using it")
    if before is not None and modified is not None and modified >= before:
        return Verdict(session, "is newer than --before")

    return Verdict(session, "")


def _modified(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _megabytes(byte_count: int) -> str:
    return f"{byte_count / 1e6:.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete the candidates. Without it nothing is removed and nothing is written.",
    )
    parser.add_argument(
        "--before",
        metavar="YYYY-MM-DD",
        help="only consider files last modified before this date.",
    )
    parser.add_argument(
        "--list-kept",
        action="store_true",
        help="also print every file being kept, and why.",
    )
    args = parser.parse_args()

    before: datetime | None = None
    if args.before:
        try:
            before = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            print(f"Not a date: {args.before!r}. Use YYYY-MM-DD.", file=sys.stderr)
            return 2

    store = ConfigStore()
    store.load()
    config = store.resolve()
    directory = archive.session_dir(config)

    sessions = archive.list_sessions(config)
    if not sessions:
        print(f"No session files in {directory}.")
        return 0

    now = datetime.now(UTC)
    verdicts = [judge(session, now=now, before=before) for session in sessions]
    candidates = [v for v in verdicts if v.prunable]
    kept = [v for v in verdicts if not v.prunable]

    print(f"Session directory: {directory}")
    print(f"  {len(sessions)} files, {_megabytes(sum(s.size_bytes for s in sessions))} in total")
    print(f"  {len(kept)} kept, {len(candidates)} empty and removable")

    protected = [v for v in kept if v.keep_because.startswith("owns a recording")]
    if protected:
        print()
        print(f"  {len(protected)} of the kept files hold no transcript but own a recording.")
        print("  Those are transcriptions that failed, not test leavings, and are left alone:")
        for verdict in protected[:20]:
            print(f"    {verdict.session.key}  {verdict.keep_because}")
        if len(protected) > 20:
            print(f"    ... and {len(protected) - 20} more")

    if args.list_kept:
        print()
        print("  Keeping:")
        for verdict in kept:
            print(f"    {verdict.session.key}  {verdict.keep_because}")

    if not candidates:
        print()
        print("Nothing to remove.")
        return 0

    reclaimed = sum(v.session.size_bytes for v in candidates)
    oldest = min(v.session.started_at for v in candidates)
    newest = max(v.session.started_at for v in candidates)
    print()
    print(f"Removable: {len(candidates)} files, {_megabytes(reclaimed)}")
    print(f"  oldest {oldest}")
    print(f"  newest {newest}")

    if not args.apply:
        print()
        print("Nothing was deleted. Re-run with --apply to remove them.")
        return 0

    deleted = 0
    failed = 0
    for verdict in candidates:
        try:
            Path(verdict.session.path).unlink()
            deleted += 1
        except OSError as exc:
            failed += 1
            print(f"  could not delete {verdict.session.key}: {exc}", file=sys.stderr)

    print()
    print(f"Deleted {deleted} files, {_megabytes(reclaimed)} reclaimed.")
    if failed:
        print(f"{failed} could not be deleted; they are listed above.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
