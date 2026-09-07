"""What the prune may delete, and — more importantly — what it may not.

The dangerous case is not the one this script was written for. Deleting a database that a test run
left behind costs nothing. Deleting a database with **no segments but a recording folder beside it**
destroys the only label on the only copy of a talk whose transcription failed, which is the case the
directory this was written for contained 28 of.

So most of what is asserted here is refusal.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import ConfigStore
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.transcript import archive
from app.services.transcript.store import TranscriptStore


def _load_script():
    """Import the script by path. It is not a package, and it should not become one for a test."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "prune_empty_sessions.py"
    spec = importlib.util.spec_from_file_location("prune_empty_sessions", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


prune = _load_script()


@pytest.fixture
def dirs(tmp_path):
    sessions = tmp_path / "sessions"
    recordings = tmp_path / "recordings"
    sessions.mkdir()
    recordings.mkdir()
    return sessions, recordings


def _config(tmp_path, sessions, recordings):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"storage.session_dir": str(sessions), "recording.recording_dir": str(recordings)})
    return store.resolve()


def write_session(directory: Path, key: str, *, segments: int = 0) -> Path:
    path = directory / f"{key}.db"
    with TranscriptStore(path, metadata=SessionMetadata(session_id=key)) as store:
        for index in range(segments):
            store.append_segment(
                Segment(
                    id=index + 1, text="a few words here", start=index * 5.0, end=index * 5.0 + 4.0
                )
            )
    _age(path)
    return path


def _age(path: Path, days: float = 3.0) -> None:
    """Push a file's mtime into the past, so the live-session guard is not what is being tested."""
    old = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    import os

    os.utime(path, (old, old))


def verdicts(tmp_path, sessions, recordings, *, before=None):
    config = _config(tmp_path, sessions, recordings)
    now = datetime.now(UTC)
    return {
        v.session.key: v
        for v in (prune.judge(s, now=now, before=before) for s in archive.list_sessions(config))
    }


# -- what may go ---------------------------------------------------------------------------


def test_a_database_with_no_segments_and_no_recording_is_removable(tmp_path, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions, "20260101-120000-aaaaaa", segments=0)

    verdict = verdicts(tmp_path, sessions, recordings)["20260101-120000-aaaaaa"]

    assert verdict.prunable
    assert verdict.keep_because == ""


# -- what may not --------------------------------------------------------------------------


def test_a_database_holding_a_transcript_is_kept(tmp_path, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions, "20260101-120000-bbbbbb", segments=4)

    verdict = verdicts(tmp_path, sessions, recordings)["20260101-120000-bbbbbb"]

    assert not verdict.prunable
    assert "holds a transcript" in verdict.keep_because


def test_an_empty_database_that_owns_a_recording_is_kept(tmp_path, dirs) -> None:
    """The 28 files this whole guard exists for: a transcription that failed, and the audio it
    failed on. An empty database is the only thing naming that folder."""
    sessions, recordings = dirs
    key = "20260101-120000-cccccc"
    write_session(sessions, key, segments=0)
    (recordings / key).mkdir()
    (recordings / key / "audio.wav").write_bytes(b"\x00" * 4096)

    verdict = verdicts(tmp_path, sessions, recordings)[key]

    assert not verdict.prunable
    assert "owns a recording" in verdict.keep_because


def test_an_empty_database_whose_folder_holds_only_a_preview_is_kept(tmp_path, dirs) -> None:
    """Anything at all in the folder protects it. The rule is emptiness, not a list of filenames
    that would need updating every time the recording layout gains one."""
    sessions, recordings = dirs
    key = "20260101-120000-dddddd"
    write_session(sessions, key, segments=0)
    (recordings / key).mkdir()
    (recordings / key / "preview.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 500)

    assert not verdicts(tmp_path, sessions, recordings)[key].prunable


def test_an_unreadable_file_is_kept(tmp_path, dirs) -> None:
    """It reports zero segments because it cannot be read, not because it is empty. Those are
    different facts and only one of them is a reason to delete something."""
    sessions, recordings = dirs
    path = sessions / "20260101-120000-eeeeee.db"
    path.write_bytes(b"this is not a database")
    _age(path)

    verdict = verdicts(tmp_path, sessions, recordings)["20260101-120000-eeeeee"]

    assert not verdict.prunable
    assert "cannot be read" in verdict.keep_because


def test_a_database_written_moments_ago_is_kept(tmp_path, dirs) -> None:
    """A session recording right now holds its database open and writes on every commit. Deleting
    that is deleting the talk in progress."""
    sessions, recordings = dirs
    write_session(sessions, "20260101-120000-ffffff", segments=0)
    # Undo the ageing the helper applies: this file is meant to look live.
    (sessions / "20260101-120000-ffffff.db").touch()

    verdict = verdicts(tmp_path, sessions, recordings)["20260101-120000-ffffff"]

    assert not verdict.prunable
    assert "just now" in verdict.keep_because


def test_before_narrows_without_widening(tmp_path, dirs) -> None:
    sessions, recordings = dirs
    write_session(sessions, "20260101-120000-0a0a0a", segments=0)
    cutoff = datetime.now(UTC) - timedelta(days=7)

    verdict = verdicts(tmp_path, sessions, recordings, before=cutoff)["20260101-120000-0a0a0a"]

    assert not verdict.prunable
    assert "newer than --before" in verdict.keep_because


# -- the command itself --------------------------------------------------------------------


def test_reporting_is_the_default_and_deletes_nothing(tmp_path, dirs, monkeypatch, capsys) -> None:
    sessions, recordings = dirs
    write_session(sessions, "20260101-120000-0b0b0b", segments=0)
    monkeypatch.setattr(sys, "argv", ["prune_empty_sessions.py"])
    monkeypatch.setattr(prune, "ConfigStore", _store_factory(tmp_path, sessions, recordings))

    assert prune.main() == 0
    assert (sessions / "20260101-120000-0b0b0b.db").exists()
    assert "Nothing was deleted" in capsys.readouterr().out


def test_apply_removes_only_the_candidates(tmp_path, dirs, monkeypatch) -> None:
    sessions, recordings = dirs
    write_session(sessions, "20260101-120000-e1e1e1", segments=0)
    write_session(sessions, "20260102-120000-f1f1f1", segments=3)
    key = "20260103-120000-d1d1d1"
    write_session(sessions, key, segments=0)
    (recordings / key).mkdir()
    (recordings / key / "audio.wav").write_bytes(b"\x00" * 2048)

    monkeypatch.setattr(sys, "argv", ["prune_empty_sessions.py", "--apply"])
    monkeypatch.setattr(prune, "ConfigStore", _store_factory(tmp_path, sessions, recordings))

    assert prune.main() == 0
    assert not (sessions / "20260101-120000-e1e1e1.db").exists()
    assert (sessions / "20260102-120000-f1f1f1.db").exists()
    assert (sessions / f"{key}.db").exists()
    assert (recordings / key / "audio.wav").exists(), "recordings are never touched"


def _store_factory(tmp_path, sessions, recordings):
    def build(*_args, **_kwargs):
        store = ConfigStore(config_path=tmp_path / "config.json")
        store.update(
            {"storage.session_dir": str(sessions), "recording.recording_dir": str(recordings)}
        )
        return store

    return build
