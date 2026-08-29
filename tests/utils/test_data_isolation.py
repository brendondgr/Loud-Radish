"""The suite must not write into the developer's own data directory.

Not a test of a feature — a test of the test suite, and it earned its place. Before the autouse
fixture in ``tests/conftest.py`` existed, every route test that started a session left a transcript
database in the real ``data/sessions``: 949 files, 690 of them empty, on the machine where this was
found. The past-sessions page lists that directory newest first, so the newest thing a user saw was
hundreds of empty test sessions and their own recordings were unreachable below them.

These assertions are what stop that returning the next time someone constructs a store without a
path, or overrides one directory and forgets the other.
"""

from __future__ import annotations

from pathlib import Path

from app.config import ConfigStore

#: The repository's own data directory, which nothing in the suite may resolve to.
REAL_DATA = Path(__file__).resolve().parents[2] / "data"


def test_a_store_with_no_config_path_writes_nowhere_near_the_real_data_directory() -> None:
    config = ConfigStore().resolve()

    for directory in (config.storage.session_dir, config.recording.recording_dir):
        assert not Path(directory).resolve().is_relative_to(REAL_DATA), directory


def test_a_store_with_its_own_config_path_is_isolated_too(tmp_path) -> None:
    """The hole that survived the first fix: a test that overrides only one of the two."""
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"recording.recording_dir": str(tmp_path / "recordings")})
    config = store.resolve()

    assert not Path(config.storage.session_dir).resolve().is_relative_to(REAL_DATA)


def test_an_explicit_override_still_wins(tmp_path) -> None:
    """Isolation must not override a test that is deliberately choosing a directory."""
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update({"storage.session_dir": str(tmp_path / "chosen")})

    assert store.resolve().storage.session_dir == str(tmp_path / "chosen")
