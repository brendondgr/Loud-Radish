"""A session with two transcription passes must say everything once (D-022).

The reported fault: stop a window session that transcribed live, wait for the post-capture pass, and
the finished result showed the language model's rewritten prose followed immediately by the entire
raw transcript again. The export and the sessions page did the same thing in a file.

Nothing was duplicating text. Both passes are *supposed* to exist — the live one is what the user
watched and what any chat citation points into, and the second one is usually better — and
``docs/api-contract.md`` already stated the rule that follows: a client showing revision 1 must
**replace** the transcript rather than merge, because the two cover the same audio with different
ids. Four call sites never applied it. These tests pin all four.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.paths import STATIC_DIR
from app.services.transcript.store import TranscriptStore
from fastapi.testclient import TestClient

LIVE_TEXT = "the live pass heard eigen value problem"
FINAL_TEXT = "the final pass heard the eigenvalue problem"


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "sessions"
    directory.mkdir()
    return directory


@pytest.fixture
def client(tmp_path: Path, sessions_dir: Path) -> TestClient:
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"storage.session_dir": str(sessions_dir)})
    app = create_app(config=store)
    with TestClient(app) as client:
        yield client


def segment(identifier: int, text: str, *, revision: int, start: float) -> Segment:
    return Segment(id=identifier, text=text, start=start, end=start + 5.0, revision=revision)


def write_two_pass_session(directory: Path, name: str = "20260101-1200-abc") -> Path:
    """A window session that transcribed live and again afterwards."""
    path = directory / f"{name}.db"
    with TranscriptStore(path, metadata=SessionMetadata(session_id=name, title="Spectra")) as store:
        store.append_segment(segment(1, LIVE_TEXT, revision=0, start=0.0))
        store.append_segment(segment(2, "and then said something else", revision=0, start=5.0))
        store.append_segment(segment(3, FINAL_TEXT, revision=1, start=0.0))
        store.append_segment(segment(4, "and then said something else", revision=1, start=5.0))
    return path


# -- the store ----------------------------------------------------------------------------


def test_the_latest_pass_is_the_transcript(tmp_path: Path) -> None:
    write_two_pass_session(tmp_path)
    with TranscriptStore(tmp_path / "20260101-1200-abc.db") as store:
        assert [s.text for s in store.latest_segments()] == [
            FINAL_TEXT,
            "and then said something else",
        ]


def test_a_one_pass_session_is_unaffected(tmp_path: Path) -> None:
    """Every ordinary session. The newest pass is the only pass."""
    with TranscriptStore(tmp_path / "one.db") as store:
        store.append_segment(segment(1, "only pass", revision=0, start=0.0))
        assert [s.text for s in store.latest_segments()] == ["only pass"]


def test_an_empty_session_has_no_transcript(tmp_path: Path) -> None:
    with TranscriptStore(tmp_path / "empty.db") as store:
        assert store.latest_segments() == []


def test_both_passes_are_still_kept(tmp_path: Path) -> None:
    """The property revisions exist for. Showing one must not delete the other."""
    write_two_pass_session(tmp_path)
    with TranscriptStore(tmp_path / "20260101-1200-abc.db") as store:
        assert len(store.all_segments()) == 4
        assert [s.text for s in store.segments_at(0)][0] == LIVE_TEXT


# -- what a reader is served ---------------------------------------------------------------


def test_a_past_session_reads_back_one_pass(client: TestClient, sessions_dir: Path) -> None:
    write_two_pass_session(sessions_dir)
    body = client.get("/api/sessions/20260101-1200-abc").json()

    texts = [item["text"] for item in body["segments"]]
    assert LIVE_TEXT not in texts, "the page showed the live pass under the final one"
    assert texts == [FINAL_TEXT, "and then said something else"]


def test_an_exported_session_says_each_sentence_once(
    client: TestClient, sessions_dir: Path
) -> None:
    """The reported fault, in the file the user keeps."""
    write_two_pass_session(sessions_dir)
    body = client.get("/api/sessions/20260101-1200-abc/export", params={"fmt": "markdown"}).text

    assert body.count("and then said something else") == 1
    assert FINAL_TEXT in body
    assert LIVE_TEXT not in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "srt", "vtt", "json"])
def test_no_export_format_repeats_the_talk(
    client: TestClient, sessions_dir: Path, fmt: str
) -> None:
    write_two_pass_session(sessions_dir)
    body = client.get("/api/sessions/20260101-1200-abc/export", params={"fmt": fmt}).text
    assert body.count("and then said something else") == 1


# -- the browser ---------------------------------------------------------------------------
#
# Read as text rather than run. Decision D-011 removed the Node toolchain on purpose, so the
# frontend has no test runner; `tests/utils/test_mode_vocabulary.py` establishes the precedent and
# the same caveat applies — these check that the guard is present in the source, not that it
# executes. The behaviour itself is verified in the browser.

TRANSCRIPT_JS = STATIC_DIR / "js" / "stores" / "transcript.js"
MAIN_JS = STATIC_DIR / "js" / "main.js"


def test_the_store_drops_segments_from_a_pass_it_is_not_showing() -> None:
    """Without this the second pass appends itself to the first, live, as it is written."""
    source = TRANSCRIPT_JS.read_text(encoding="utf-8")
    assert "_isShown(segment)" in source
    assert re.search(r"if \(!this\._isShown\(segment\)\) return false;", source), (
        "commit() no longer filters by revision"
    )
    assert "this.revision" in source


def test_a_finished_second_pass_replaces_the_transcript() -> None:
    """`showLatestRevision` resets into the new revision; it must never merge into the old one."""
    source = MAIN_JS.read_text(encoding="utf-8")
    assert "showLatestRevision" in source
    assert "transcript.reset(revision)" in source
