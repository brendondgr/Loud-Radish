"""The capture routes the monitor pane reads.

The pane showed nothing through an entire recording while the preview JPEG was being written to
disk correctly once a second. The cause was a design one: `capture.state` was emitted **once**, at
the moment capture began — a fact broadcast into a lossy channel with no way to ask again. Four
ordinary events lost it permanently: a page loaded after the emit, a socket reconnect, a second tab,
or the emit racing the recorder into existence.

The fix is state-plus-event, and these tests cover the state half — an authoritative `GET` the
client can reconcile against on every connect, and a preview route that distinguishes "not yet"
from "never" without ever serving half a picture.
"""

from __future__ import annotations

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.routes.capture import JPEG_END
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    app = create_app(config=ConfigStore(config_path=tmp_path / "config.json"))
    with TestClient(app) as client:
        yield client


class FakeRecorderState:
    def __init__(self, preview_path: str = "") -> None:
        self.preview_path = preview_path
        self.video_path = ""
        self.running = True
        self.window_closed = False
        self.failed = False
        self.error = ""
        self.bytes_written = 0
        self.duration_s = 0.0


class FakeRecorder:
    def __init__(self, preview_path: str = "") -> None:
        self.state = FakeRecorderState(preview_path)
        self.is_running = True


def test_the_state_endpoint_answers_when_nothing_is_recording(client: TestClient) -> None:
    """The authoritative answer must exist even when the answer is "nothing".

    A client that can only learn about capture from an event it may not have been present for is a
    client that shows an empty pane through a whole recording.
    """
    response = client.get("/api/capture/state")

    assert response.status_code == 200
    body = response.json()
    assert body["recording"] is False


def test_the_state_endpoint_reports_a_running_capture(client: TestClient) -> None:
    manager = client.app.state.session_manager
    manager._recorder = FakeRecorder(preview_path="/tmp/does-not-matter.jpg")

    body = client.get("/api/capture/state").json()

    assert body["recording"] is True
    assert body["preview"] is True


def test_no_preview_is_a_404_that_is_not_cached(client: TestClient) -> None:
    """**A cached 404 is sticky**, and that is the whole bug in miniature.

    A browser that caches "there is no preview" will not ask again for a while, so the pane stays
    empty for the rest of the recording even though frames began arriving a second later. The
    header is what makes "not yet" recoverable rather than permanent.
    """
    response = client.get("/api/capture/preview.jpg")

    assert response.status_code == 404
    assert "no-store" in response.headers.get("cache-control", "")


def test_a_complete_frame_is_served_uncached(client: TestClient, tmp_path) -> None:
    frame = tmp_path / "preview.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64 + JPEG_END)
    client.app.state.session_manager._recorder = FakeRecorder(preview_path=str(frame))

    response = client.get("/api/capture/preview.jpg")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert "no-store" in response.headers.get("cache-control", "")
    assert response.content.endswith(JPEG_END)


def test_a_half_written_frame_is_not_served(client: TestClient, tmp_path) -> None:
    """`multifilesink` rewrites this file in place about once a second.

    A request arriving mid-write gets a truncated JPEG, which renders as a torn or blank frame and
    looks exactly like a broken capture. A complete JPEG ends with the end-of-image marker; one
    that does not is a frame caught in the middle of being written, and the honest answer is
    "not yet" rather than half a picture.
    """
    frame = tmp_path / "preview.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)  # no end-of-image marker
    client.app.state.session_manager._recorder = FakeRecorder(preview_path=str(frame))

    response = client.get("/api/capture/preview.jpg")

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "preview-incomplete"


def test_a_missing_file_is_a_404_rather_than_a_crash(client: TestClient, tmp_path) -> None:
    """The recorder can report a path before the first frame exists."""
    client.app.state.session_manager._recorder = FakeRecorder(
        preview_path=str(tmp_path / "never-written.jpg")
    )

    assert client.get("/api/capture/preview.jpg").status_code == 404
