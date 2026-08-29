"""One directory per recording — naming, containment, and the migration of flat files.

The layout is what every later reader depends on: the recordings list, the past-sessions media
indicators, and the web-app export all answer "what did this recording produce" by listing one
directory. So the tests here are about the two things that would make that unsafe — a key that
escapes the recordings root, and a migration that loses a file.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from app.services.recording import layout as layout_module
from app.services.recording import layout_for, migrate_flat_recordings, resolve_recording

STARTED = datetime(2026, 8, 29, 17, 41, 13)
SESSION = "d60b37a9e3c4"
KEY = "20260829-174113-d60b37a9e3c4"


def test_a_recording_is_named_for_the_moment_it_started(tmp_path) -> None:
    layout = layout_for(tmp_path, STARTED, SESSION)

    assert layout.key == KEY
    assert layout.directory == tmp_path / KEY
    assert layout.audio.name == "audio.wav"
    assert layout.preview.name == "preview.jpg"
    assert layout.video("webm").name == "video.webm"
    # With or without the dot, because callers get the extension from the encoder probe either way.
    assert layout.video(".mkv").name == "video.mkv"


def test_the_folder_name_matches_the_transcript_databases_stem(tmp_path) -> None:
    """The whole join between a recording and its transcript is this equality."""
    layout = layout_for(tmp_path, STARTED, SESSION)
    database_stem = f"{STARTED.strftime('%Y%m%d-%H%M%S')}-{SESSION}"

    assert layout.key == database_stem


def test_the_directory_is_created_only_when_asked(tmp_path) -> None:
    layout = layout_for(tmp_path, STARTED, SESSION)
    assert not layout.exists()

    layout.ensure()
    assert layout.directory.is_dir()


@pytest.mark.parametrize(
    "key",
    ["../etc", "..", "20260829-174113-d60b37a9e3c4/../../etc", "not-a-key", ""],
)
def test_a_key_cannot_escape_the_recordings_directory(tmp_path, key) -> None:
    assert resolve_recording(tmp_path, key) is None


def test_a_real_key_resolves(tmp_path) -> None:
    (tmp_path / KEY).mkdir()
    layout = resolve_recording(tmp_path, KEY)

    assert layout is not None
    assert layout.key == KEY


def test_the_muxed_video_is_preferred_because_it_is_the_one_with_sound(tmp_path) -> None:
    layout = layout_for(tmp_path, STARTED, SESSION)
    layout.ensure()
    (layout.directory / "video.webm").write_bytes(b"raw")
    (layout.directory / "video-with-audio.webm").write_bytes(b"muxed")

    assert layout.existing_video() == layout.directory / "video-with-audio.webm"


def test_an_empty_video_file_does_not_count_as_a_video(tmp_path) -> None:
    layout = layout_for(tmp_path, STARTED, SESSION)
    layout.ensure()
    (layout.directory / "video.webm").write_bytes(b"")

    assert layout.existing_video() is None


def test_a_bare_wav_header_does_not_count_as_audio(tmp_path) -> None:
    """A sink that opened and captured nothing leaves 44 bytes. That is not a recording."""
    layout = layout_for(tmp_path, STARTED, SESSION)
    layout.ensure()
    layout.audio.write_bytes(b"\x00" * 44)

    assert not layout.has_audio()

    layout.audio.write_bytes(b"\x00" * 200)
    assert layout.has_audio()


def test_recordings_are_listed_newest_first_by_key_not_by_mtime(tmp_path) -> None:
    for key in ("20260101-000000-aaaaaaaaaaaa", "20260829-174113-d60b37a9e3c4"):
        (tmp_path / key).mkdir()
    # Touching the older one last must not reorder the list: a transcription pass writing a sidecar
    # into a week-old folder is exactly this situation.
    (tmp_path / "20260101-000000-aaaaaaaaaaaa" / "audio.json").write_text("{}")
    (tmp_path / "not-a-recording").mkdir()

    keys = [layout.key for layout in layout_module.iter_recordings(tmp_path)]

    assert keys == ["20260829-174113-d60b37a9e3c4", "20260101-000000-aaaaaaaaaaaa"]


def test_flat_files_are_grouped_into_folders_without_losing_any(tmp_path) -> None:
    names = [
        f"{KEY}.wav",
        f"{KEY}.json",
        f"{KEY}.webm",
        f"{KEY}-preview.jpg",
        f"{KEY}-with-audio.webm",
    ]
    for name in names:
        (tmp_path / name).write_text(name)

    moved = migrate_flat_recordings(tmp_path)

    assert moved == len(names)
    folder = tmp_path / KEY
    assert sorted(path.name for path in folder.iterdir()) == [
        "audio.json",
        "audio.wav",
        "preview.jpg",
        "video-with-audio.webm",
        "video.webm",
    ]
    # Contents survive the move, which is the only thing that actually matters here.
    assert (folder / "audio.wav").read_text() == f"{KEY}.wav"


def test_migration_leaves_anything_it_does_not_recognise_alone(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("mine")
    (tmp_path / "README").write_text("mine")

    assert migrate_flat_recordings(tmp_path) == 0
    assert (tmp_path / "notes.txt").read_text() == "mine"
    assert (tmp_path / "README").read_text() == "mine"


def test_migration_is_idempotent(tmp_path) -> None:
    (tmp_path / f"{KEY}.wav").write_text("audio")

    assert migrate_flat_recordings(tmp_path) == 1
    assert migrate_flat_recordings(tmp_path) == 0
    assert (tmp_path / KEY / "audio.wav").read_text() == "audio"


def test_migration_never_overwrites_a_file_already_in_the_folder(tmp_path) -> None:
    (tmp_path / KEY).mkdir()
    (tmp_path / KEY / "audio.wav").write_text("already here")
    (tmp_path / f"{KEY}.wav").write_text("loose")

    assert migrate_flat_recordings(tmp_path) == 0
    assert (tmp_path / KEY / "audio.wav").read_text() == "already here"
    assert (tmp_path / f"{KEY}.wav").exists()


def test_a_missing_recordings_directory_is_not_an_error(tmp_path) -> None:
    assert migrate_flat_recordings(tmp_path / "absent") == 0
    assert list(layout_module.iter_recordings(tmp_path / "absent")) == []
