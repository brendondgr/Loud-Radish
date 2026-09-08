"""Whether a finished session has anything to export (D-058).

The interface opens its export window the moment a recording stops, so the user does not have to
find the Recordings page and choose between two download links. It opened for **every** session —
including a plain live transcription, which writes no media at all — so an ordinary talk ended with
a dialog offering five video qualities above the words *"This recording has no video"*. Reported,
with a screenshot.

`session.stopped` now carries `has_media`, and the answer comes from **the recording folder rather
than from the mode**. That distinction is the whole point: `live` normally writes nothing but does
write audio when `storage.retain_audio` is on, and a `recorded` session whose microphone never
opened writes nothing despite its mode saying otherwise. Only the folder knows which happened.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.config import ConfigStore
from app.models.session import SessionMetadata
from app.services.recording.layout import layout_for
from app.services.session.manager import SessionManager


@pytest.fixture
def manager(tmp_path):
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "recording.recording_dir": str(tmp_path / "recordings"),
            "storage.session_dir": str(tmp_path / "sessions"),
        },
        layer="user",
    )
    handle = SessionManager(store, emit=lambda _name, _payload: None)
    handle._metadata = SessionMetadata(
        session_id="a1b2c3d4e5f6", started_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    )
    return handle


def folder(manager):  # noqa: ANN001
    config = manager._config.resolve()
    return layout_for(
        config.recording.recording_dir,
        manager._metadata.started_at,
        manager._metadata.session_id,
    )


# -- nothing to export --------------------------------------------------------------------------


def test_a_session_that_wrote_nothing_has_no_media(manager) -> None:
    """**The reported fault.** A live transcription leaves no recording folder at all, and used to
    open an export window regardless."""
    assert manager._has_media() is False


def test_a_folder_with_no_media_in_it_has_no_media(manager) -> None:
    """A `recorded` session whose microphone never opened: the folder exists and is empty."""
    folder(manager).ensure()

    assert manager._has_media() is False


def test_a_transcript_alone_is_not_something_to_export_here(manager) -> None:
    """The export window is about audio and video. A transcript is exported from elsewhere, and
    offering video qualities for one is the fault this closes."""
    layout = folder(manager)
    layout.ensure()
    layout.sidecar.write_text("{}", encoding="utf-8")

    assert manager._has_media() is False


def test_an_empty_audio_file_is_not_playable(manager) -> None:
    """A zero-length wav is what a capture that opened and captured nothing leaves behind."""
    layout = folder(manager)
    layout.ensure()
    layout.audio.write_bytes(b"")

    assert manager._has_media() is False


# -- something to export ------------------------------------------------------------------------


def test_a_recording_with_audio_has_media(manager, tmp_path) -> None:
    from app.services.recording.sink import WavSink

    layout = folder(manager)
    layout.ensure()
    sink = WavSink(layout.audio)
    try:
        import numpy as np

        sink.write(np.zeros(16_000, dtype=np.float32))
    finally:
        sink.close()

    assert manager._has_media() is True


def test_a_recording_with_video_has_media(manager) -> None:
    layout = folder(manager)
    layout.ensure()
    layout.video("webm").write_bytes(b"not really a video, but it is there")

    assert manager._has_media() is True


# -- it is asked of the folder, not the mode ------------------------------------------------------


def test_the_answer_does_not_depend_on_the_capture_mode(manager) -> None:
    """**Why this is not `mode != "live"`.** `storage.retain_audio` makes a live session write
    audio, and a mode check would then hide the export window for a recording that exists."""
    import numpy as np
    from app.services.recording.sink import WavSink

    manager._metadata.mode = "live"
    layout = folder(manager)
    layout.ensure()
    sink = WavSink(layout.audio)
    try:
        sink.write(np.zeros(16_000, dtype=np.float32))
    finally:
        sink.close()

    assert manager._has_media() is True, (
        "a live session that kept its audio has something to export"
    )


def test_a_missing_recordings_directory_is_an_answer_not_a_crash(manager, tmp_path) -> None:
    """Stopping a session must not fail because the folder it would have written to is gone."""
    manager._config.update({"recording.recording_dir": str(tmp_path / "nowhere")}, layer="user")

    assert manager._has_media() is False
