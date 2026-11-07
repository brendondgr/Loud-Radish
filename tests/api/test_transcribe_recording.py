"""Transcribing a past recording that has no transcript, on the user's approval (D-076).

A recording comes back to the main screen only with a transcript. One with only audio or video is
transcribed first, and **only when the user approves it** — so what is tested here is the plan the
approval dialog shows, that nothing starts until the POST, the three sources (a WAV, a video's
sound, a folder with no database at all), and the recording opening itself when asked to.

Runs the real batch pass against the scripted mock speech model, so no model is downloaded.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.main import create_app
from app.models.session import SessionMetadata
from app.services.audio.formats import SAMPLE_RATE
from app.services.transcript.store import TranscriptStore
from fastapi.testclient import TestClient

WAV_KEY = "20261104-100000-c3c3c3c3c3c3"
VIDEO_KEY = "20261105-100000-d4d4d4d4d4d4"
SILENT_KEY = "20261106-100000-e5e5e5e5e5e5"


@pytest.fixture
def dirs(tmp_path) -> tuple[Path, Path]:
    sessions, recordings = tmp_path / "sessions", tmp_path / "recordings"
    sessions.mkdir()
    recordings.mkdir()
    return sessions, recordings


@pytest.fixture
def client(tmp_path, dirs):
    sessions, recordings = dirs
    store = ConfigStore(config_path=tmp_path / "config.json")
    store.update(
        {
            "storage.session_dir": str(sessions),
            "recording.recording_dir": str(recordings),
            "storage.reopen_last_session": False,
            "asr.backend": "mock",
        }
    )
    with TestClient(create_app(config=store)) as client:
        yield client


def tone(seconds: float = 2.0, rate: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * 220 * t) * 0.4 * 32767).astype(np.int16)


def write_wav(folder: Path, seconds: float = 2.0) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "audio.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(tone(seconds).tobytes())
    return path


def write_video(path: Path, *, sound: bool, seconds: int = 2) -> Path:
    av = pytest.importorskip("av")
    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w", format="matroska") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        audio = container.add_stream("pcm_s16le", rate=SAMPLE_RATE) if sound else None
        for _ in range(10 * seconds):
            frame = av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")
            for packet in video.encode(frame):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        if audio is not None:
            frame = av.AudioFrame.from_ndarray(
                tone(seconds).reshape(1, -1), format="s16", layout="mono"
            )
            frame.sample_rate = SAMPLE_RATE
            for packet in audio.encode(frame):
                container.mux(packet)
            for packet in audio.encode():
                container.mux(packet)
    return path


def write_empty_session(sessions: Path, key: str) -> Path:
    path = sessions / f"{key}.db"
    TranscriptStore(path, metadata=SessionMetadata(session_id=key.rsplit("-", 1)[1])).close()
    return path


def wait_for_pass(client: TestClient, timeout: float = 20.0) -> str:
    deadline = time.monotonic() + timeout
    jobs = client.app.state.session_manager.jobs
    while time.monotonic() < deadline:
        job = jobs.current
        if job is not None and not job.is_running:
            # The runner releases the store, and opens the recording, just after the state flips.
            time.sleep(0.2)
            return str(job.state)
        time.sleep(0.05)
    raise AssertionError("the pass did not finish")


# -- the listing says what can be done -----------------------------------------------------------


def test_a_folder_with_no_database_is_listed_as_transcribable(client, dirs) -> None:
    write_wav(dirs[1] / WAV_KEY)

    [row] = client.get("/api/sessions").json()["sessions"]

    assert row["key"] == WAV_KEY
    assert row["orphan"] is True
    assert row["title"].startswith("Recording on ")
    assert row["media"]["openable"] is False
    assert row["media"]["transcribable"] is True
    assert row["media"]["transcribe_source"] == "wav"


def test_a_video_with_no_sound_says_why_it_cannot_be_transcribed(client, dirs) -> None:
    write_video(dirs[1] / SILENT_KEY / "video.mkv", sound=False)

    [row] = client.get("/api/sessions").json()["sessions"]

    assert row["media"]["transcribable"] is False
    assert row["media"]["reason"] == "This video has no sound to transcribe."


# -- the plan the dialog shows, and that it starts nothing ---------------------------------------


def test_the_plan_describes_the_work_and_starts_nothing(client, dirs) -> None:
    write_empty_session(dirs[0], WAV_KEY)
    write_wav(dirs[1] / WAV_KEY, seconds=3.0)

    plan = client.get(f"/api/sessions/{WAV_KEY}/transcribe/plan").json()

    assert plan["source"] == "wav"
    assert plan["seconds"] == pytest.approx(3.0, abs=0.1)
    assert plan["refusal"] is None
    assert client.app.state.session_manager.jobs.current is None


def test_a_recording_with_a_transcript_needs_no_pass(client, dirs) -> None:
    from app.models.segment import Segment

    path = write_empty_session(dirs[0], WAV_KEY)
    with TranscriptStore(path) as store:
        store.append_segment(Segment(id=1, text="already here", start=0.0, end=1.0))

    plan = client.get(f"/api/sessions/{WAV_KEY}/transcribe/plan").json()
    assert plan["refusal"]["code"] == "already-transcribed"
    assert client.post(f"/api/sessions/{WAV_KEY}/transcribe").status_code == 409


def test_an_unknown_recording_is_a_404(client) -> None:
    assert client.get(f"/api/sessions/{WAV_KEY}/transcribe/plan").status_code == 404


def test_approving_is_refused_while_a_session_records(client, dirs) -> None:
    write_wav(dirs[1] / WAV_KEY)
    manager = client.app.state.session_manager
    manager._metadata = SessionMetadata(session_id="live")
    try:
        response = client.post(f"/api/sessions/{WAV_KEY}/transcribe")
        assert response.status_code == 409
        assert response.json()["detail"]["error"]["code"] == "session-running"
    finally:
        manager._metadata = None


# -- the approved pass ----------------------------------------------------------------------------


def test_an_approved_wav_is_transcribed_and_opened(client, dirs) -> None:
    write_empty_session(dirs[0], WAV_KEY)
    write_wav(dirs[1] / WAV_KEY)

    response = client.post(f"/api/sessions/{WAV_KEY}/transcribe", json={"open_when_done": True})

    assert response.status_code == 200, response.text
    assert response.json()["source"] == "wav"
    assert wait_for_pass(client) == "done"
    state = client.get("/api/session").json()
    assert state["viewing"]["key"] == WAV_KEY
    assert client.get("/api/transcript/since/0").json()["segments"]


def test_a_folder_with_no_database_gets_one_under_its_own_key(client, dirs) -> None:
    write_wav(dirs[1] / WAV_KEY)

    client.post(f"/api/sessions/{WAV_KEY}/transcribe", json={"open_when_done": False})
    wait_for_pass(client)

    assert (dirs[0] / f"{WAV_KEY}.db").is_file()
    [row] = client.get("/api/sessions").json()["sessions"]
    assert row["orphan"] is False
    assert row["segments"] > 0
    # Not opened: the checkbox was off.
    assert client.get("/api/session").json()["viewing"] is None


def test_a_videos_sound_is_taken_out_and_transcribed(client, dirs) -> None:
    write_video(dirs[1] / VIDEO_KEY / "video-with-audio.mkv", sound=True)

    plan = client.get(f"/api/sessions/{VIDEO_KEY}/transcribe/plan").json()
    assert plan["source"] == "video"

    response = client.post(f"/api/sessions/{VIDEO_KEY}/transcribe")
    assert response.status_code == 200, response.text
    assert wait_for_pass(client) == "done"

    row = next(r for r in client.get("/api/sessions").json()["sessions"] if r["key"] == VIDEO_KEY)
    assert row["segments"] > 0
    assert row["media"]["video"] is True
    assert client.get("/api/session").json()["viewing"]["key"] == VIDEO_KEY
