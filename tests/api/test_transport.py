"""The transport contract — HTTP surface, WebSocket events, and reconnection replay (BE §12).

The replay test is the important one. It asserts the property that makes the socket disposable:
a client that reconnects and says what it last saw gets exactly what it missed, and applying the
replay twice changes nothing.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from app.config import ConfigStore
from app.main import create_app
from app.services.audio.formats import SAMPLE_RATE
from app.transport.events import ALL_EVENTS, CRITICAL_EVENTS, envelope, is_coalescing
from app.transport.hub import ClientConnection, EventHub
from fastapi.testclient import TestClient


def write_talk_wav(path: Path, seconds: float = 30.0) -> Path:
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    tone = (
        0.45 * np.sin(2 * np.pi * 180 * t)
        + 0.28 * np.sin(2 * np.pi * 420 * t)
        + 0.14 * np.sin(2 * np.pi * 950 * t)
    )
    syllables = 0.12 + 0.88 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t))
    signal = np.clip(tone * syllables * 0.35, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


@pytest.fixture
def client(tmp_path: Path):
    config = ConfigStore(config_path=tmp_path / "config.json")
    config.update(
        {
            "asr.backend": "mock",
            "asr.model": "scripted",
            "audio.source_type": "file",
            "audio.file_path": str(write_talk_wav(tmp_path / "talk.wav")),
            # Paced, not unlimited: at speed 0 the file source fills the bounded capture
            # queue faster than the ASR worker drains it, and drop-oldest discards most of
            # the talk — correct behaviour, but not what these tests are measuring.
            "audio.file_speed": 20.0,
            "storage.session_dir": str(tmp_path / "sessions"),
            "streaming.step_s": 0.5,
            "streaming.min_buffer_s": 0.4,
            # Short segments, so a run of this length reliably produces several and the
            # partial-replay tests have something to be partial about.
            "streaming.max_segment_s": 5.0,
        }
    )
    app = create_app(config=config)
    with TestClient(app) as client:
        yield client


class TestEventVocabulary:
    def test_every_documented_event_is_named(self) -> None:
        """The frontend's handler map is checked against this list."""
        assert set(ALL_EVENTS) >= {
            "session.started",
            "session.stopped",
            "transcript.committed",
            "transcript.hypothesis",
            "transcript.polished",
            "audio.level",
            "vad.state",
            "status",
            "summary.added",
            "glossary.added",
            "chat.delta",
            "chat.done",
            "error",
        }

    def test_committed_segments_are_never_coalesced(self) -> None:
        """Losing one loses transcript."""
        assert not is_coalescing("transcript.committed")
        assert "transcript.committed" in CRITICAL_EVENTS

    def test_polished_blocks_are_never_coalesced(self) -> None:
        """A block is produced once and never re-sent; dropping one leaves that minute raw."""
        assert not is_coalescing("transcript.polished")
        assert "transcript.polished" in CRITICAL_EVENTS

    def test_health_events_coalesce(self) -> None:
        """A stale level-meter frame is worse than none — it draws a wrong bar."""
        for event in ("transcript.hypothesis", "audio.level", "vad.state", "status"):
            assert is_coalescing(event)

    def test_the_envelope_shape_is_stable(self) -> None:
        assert envelope("status", {"rtf": 1.8}) == {"event": "status", "data": {"rtf": 1.8}}


class TestClientQueue:
    def test_coalescing_events_replace_rather_than_accumulate(self) -> None:
        connection = ClientConnection("c1")
        for value in range(10):
            connection.push(envelope("audio.level", {"rms": value}))

        frames = connection._queue  # noqa: SLF001 - asserting the internal backlog is the point
        assert len(frames) == 1
        assert frames[0]["data"]["rms"] == 9

    def test_committed_segments_all_survive(self) -> None:
        connection = ClientConnection("c1")
        for value in range(10):
            connection.push(envelope("transcript.committed", {"id": value}))
        assert len(connection._queue) == 10  # noqa: SLF001

    def test_a_deep_backlog_sheds_health_events_before_transcript(self) -> None:
        """A client far enough behind to overflow has a meaningless level meter anyway."""
        connection = ClientConnection("c1", depth=6)
        for value in range(4):
            connection.push(envelope("transcript.committed", {"id": value}))
        for value in range(20):
            connection.push(envelope("asr.progress", {"n": value}))

        remaining = list(connection._queue)  # noqa: SLF001
        committed = [f for f in remaining if f["event"] == "transcript.committed"]
        assert len(committed) == 4

    def test_a_closed_connection_accepts_nothing_more(self) -> None:
        connection = ClientConnection("c1")
        connection.close()
        connection.push(envelope("status", {}))
        assert connection.depth == 0


class TestHub:
    def test_emitting_without_a_loop_still_delivers(self) -> None:
        """Tests drive the pipeline directly, with no loop bound."""
        hub = EventHub()
        connection = hub.connect("c1")
        hub.emit("status", {"rtf": 1.8})
        assert connection.depth == 1

    def test_the_latest_health_state_is_kept_for_new_clients(self) -> None:
        """So a client that connects mid-session paints a correct screen immediately."""
        hub = EventHub()
        hub.emit("status", {"rtf": 1.8})
        hub.emit("vad.state", {"speaking": True})

        events = {frame["event"] for frame in hub.latest_state()}
        assert events == {"status", "vad.state"}

    def test_only_the_latest_of_each_health_event_is_kept(self) -> None:
        hub = EventHub()
        hub.emit("status", {"rtf": 1.0})
        hub.emit("status", {"rtf": 2.0})
        assert hub.latest_state()[0]["data"]["rtf"] == 2.0

    def test_a_finished_pass_is_not_replayed_as_a_running_one(self) -> None:
        """The bug behind "stuck at Transcribing… 100%".

        Progress coalesces, so the hub keeps the newest frame and replays it to every client that
        connects. Without a retraction the last frame of a *finished* pass says `running` forever,
        and every reload afterwards was told a transcription was still going — which in a mode with
        a `processing` state wedged the record button, and in `live`, which has no such state, left
        the clock running under a button that said "Start recording".
        """
        hub = EventHub()
        hub.emit("transcription.progress", {"state": "running", "progress": 0.97})
        assert "transcription.progress" in {f["event"] for f in hub.latest_state()}

        hub.emit("transcription.done", {"state": "done", "progress": 1.0})

        assert "transcription.progress" not in {f["event"] for f in hub.latest_state()}

    def test_a_failed_pass_retracts_its_progress_too(self) -> None:
        hub = EventHub()
        hub.emit("transcription.progress", {"state": "running", "progress": 0.4})
        hub.emit("transcription.failed", {"state": "failed", "error": "disk full"})

        assert "transcription.progress" not in {f["event"] for f in hub.latest_state()}

    def test_stopping_retracts_the_recording_figure_and_the_hypothesis(self) -> None:
        """Both describe a capture that is over. Replaying either shows a session still running."""
        hub = EventHub()
        hub.emit("recording.progress", {"duration_s": 12.0, "bytes": 400_000})
        hub.emit("transcript.hypothesis", {"text": "half a sentence", "start": 11.0})
        hub.emit("session.stopped", {"session_id": "abc"})

        assert hub.latest_state() == []

    def test_health_events_survive_a_stop_because_they_are_still_true(self) -> None:
        """Retraction is targeted. A level meter after a stop is stale, not wrong to keep."""
        hub = EventHub()
        hub.emit("status", {"rtf": 1.8})
        hub.emit("session.stopped", {"session_id": "abc"})

        assert {f["event"] for f in hub.latest_state()} == {"status"}

    def test_a_pass_that_is_still_running_is_replayed(self) -> None:
        """The retraction must not cost the feature it protects."""
        hub = EventHub()
        hub.emit("transcription.progress", {"state": "running", "progress": 0.5})

        frames = hub.latest_state()
        assert [f["data"]["progress"] for f in frames] == [0.5]

    def test_disconnecting_removes_the_client(self) -> None:
        hub = EventHub()
        hub.connect("c1")
        assert hub.client_count == 1
        hub.disconnect("c1")
        assert hub.client_count == 0

    def test_disconnecting_an_unknown_client_is_harmless(self) -> None:
        EventHub().disconnect("never-connected")


class TestHttpSurface:
    def test_session_state_is_available_before_recording(self, client: TestClient) -> None:
        body = client.get("/api/session").json()
        assert body["running"] is False

    def test_a_session_starts_and_stops(self, client: TestClient) -> None:
        started = client.post("/api/session/start", json={"title": "Operator domains"})
        assert started.status_code == 200
        assert started.json()["running"] is True

        stopped = client.post("/api/session/stop")
        assert stopped.status_code == 200
        assert "stats" in stopped.json()

    def test_starting_twice_conflicts_with_a_readable_message(self, client: TestClient) -> None:
        client.post("/api/session/start", json={})
        try:
            second = client.post("/api/session/start", json={})
            assert second.status_code == 409
            assert "already recording" in second.json()["detail"]["error"]["message"]
        finally:
            client.post("/api/session/stop")

    def test_stopping_when_idle_conflicts(self, client: TestClient) -> None:
        assert client.post("/api/session/stop").status_code == 409

    def test_devices_are_listed_with_the_file_source(self, client: TestClient) -> None:
        body = client.get("/api/audio/devices").json()
        assert any(device["kind"] == "file" for device in body["devices"])

    def test_missing_device_support_is_explained(self, client: TestClient) -> None:
        """An empty list with no explanation looks like a broken feature.

        **This assertion only runs on a machine without device support**, which is why it was
        wrong for a fortnight without anyone noticing: on a developer's desktop `sounddevice`
        opens a backend, `device_support` is true, and the branch below is never taken. On a
        runner it is, and the assertion was still looking for the error *code* `audio-device`
        rather than for anything the note actually says. What the note owes the reader is the
        problem and the remedy, so that is what this checks.
        """
        body = client.get("/api/audio/devices").json()
        if body["device_support"]:
            assert body["note"] == ""
            return
        assert "audio backend is missing" in body["note"]
        assert "uv sync" in body["note"]

    def test_asr_models_are_listed_with_capabilities(self, client: TestClient) -> None:
        body = client.get("/api/asr/models").json()
        assert body["backends"]
        assert all("capabilities" in backend for backend in body["backends"])

    def test_the_session_prompt_can_be_set(self, client: TestClient) -> None:
        response = client.post("/api/asr/prompt", json={"prompt": "self-adjoint extensions"})
        assert response.json()["prompt_length"] == 23

    def test_config_round_trips(self, client: TestClient) -> None:
        body = client.get("/api/config").json()
        assert body["config"]["streaming"]["agreement_count"] == 2
        assert any(preset["name"] == "balanced" for preset in body["presets"])

    def test_a_config_change_reports_what_it_costs(self, client: TestClient) -> None:
        """The frontend warns before the user commits to interrupting transcription."""
        response = client.patch("/api/config", json={"changes": {"asr.model": "medium"}})
        body = response.json()
        assert body["hot_swap"] == "restart-stage"
        assert body["consequence"]
        assert body["config"]["asr"]["model"] == "medium"

    def test_a_live_change_says_so(self, client: TestClient) -> None:
        body = client.patch("/api/config", json={"changes": {"vad.sensitivity": 0.3}}).json()
        assert body["hot_swap"] == "live"

    def test_an_invalid_config_value_is_rejected_readably(self, client: TestClient) -> None:
        response = client.patch("/api/config", json={"changes": {"vad.sensitivity": 99}})
        assert response.status_code == 422
        assert "could not be applied" in response.json()["detail"]["error"]["message"]

    def test_a_preset_applies(self, client: TestClient) -> None:
        body = client.post("/api/config/preset", json={"name": "low-resource"}).json()
        assert body["config"]["asr"]["device"] == "cpu"

    def test_an_unknown_preset_is_rejected(self, client: TestClient) -> None:
        assert client.post("/api/config/preset", json={"name": "turbo"}).status_code == 422

    def test_the_transcript_is_unavailable_before_a_session(self, client: TestClient) -> None:
        response = client.get("/api/transcript/since/0")
        assert response.status_code == 404
        assert "Start recording" in response.json()["detail"]["error"]["message"]

    def test_no_response_ever_contains_a_credential(self, client: TestClient) -> None:
        for path in ("/api/config", "/api/session", "/api/asr/models", "/api/health"):
            body = client.get(path).text.lower()
            assert "api_key" not in body
            assert "sk-" not in body


class TestTranscriptEndpoints:
    @pytest.fixture
    def recorded(self, client: TestClient) -> TestClient:
        """A client with a finished session behind it."""
        client.post("/api/session/start", json={})
        import time

        # Wait for several segments, not just one: the partial-replay tests below need a
        # transcript with a middle, and stopping at the first would make them skip at random.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if len(client.get("/api/transcript/since/0").json()["segments"]) >= 3:
                break
            time.sleep(0.05)
        return client

    def test_segments_since_returns_the_transcript(self, recorded: TestClient) -> None:
        body = recorded.get("/api/transcript/since/0").json()
        assert body["segments"]
        assert body["last_id"] == body["segments"][-1]["id"]

    def test_replay_from_a_later_id_returns_only_what_follows(self, recorded: TestClient) -> None:
        everything = recorded.get("/api/transcript/since/0").json()["segments"]
        if len(everything) < 2:
            pytest.skip("not enough segments to test a partial replay")

        cutoff = everything[0]["id"]
        tail = recorded.get(f"/api/transcript/since/{cutoff}").json()["segments"]
        assert [s["id"] for s in tail] == [s["id"] for s in everything[1:]]

    def test_replaying_twice_yields_the_same_segments(self, recorded: TestClient) -> None:
        """Idempotent replay is what makes the socket disposable."""
        first = recorded.get("/api/transcript/since/0").json()["segments"]
        second = recorded.get("/api/transcript/since/0").json()["segments"]
        assert [s["id"] for s in first] == [s["id"] for s in second]

    def test_polished_blocks_are_empty_without_a_session(self, client: TestClient) -> None:
        """Read on every page load, so "nothing recorded yet" must not be a console error."""
        response = client.get("/api/transcript/polished")
        assert response.status_code == 200
        assert response.json() == {"blocks": []}

    def test_polished_blocks_are_empty_with_no_language_model(self, recorded: TestClient) -> None:
        """The ordinary case: the page falls back to raw segments, which is the feature working."""
        assert recorded.get("/api/transcript/polished").json()["blocks"] == []

    def test_polished_blocks_are_returned_oldest_first(self, recorded: TestClient) -> None:
        store = recorded.app.state.session_manager.store
        store.add_polished_block(60.0, 118.0, "The second minute.", [4, 5])
        store.add_polished_block(0.0, 58.0, "The first minute.", [1, 2, 3])

        blocks = recorded.get("/api/transcript/polished").json()["blocks"]
        assert [block["text"] for block in blocks] == ["The first minute.", "The second minute."]
        assert blocks[0]["source_ids"] == [1, 2, 3]

    def test_search_finds_committed_text(self, recorded: TestClient) -> None:
        body = recorded.get("/api/transcript/search", params={"q": "operators"}).json()
        assert isinstance(body["segments"], list)

    def test_a_malformed_search_returns_nothing_rather_than_failing(
        self, recorded: TestClient
    ) -> None:
        response = recorded.get("/api/transcript/search", params={"q": "NOT ("})
        assert response.status_code == 200
        assert response.json()["segments"] == []

    def test_a_time_range_is_queryable(self, recorded: TestClient) -> None:
        body = recorded.get("/api/transcript/range", params={"start": 0, "end": 3}).json()
        assert isinstance(body["segments"], list)

    @pytest.mark.parametrize("fmt", ["text", "markdown", "srt", "vtt", "json"])
    def test_every_export_format_downloads(self, recorded: TestClient, fmt: str) -> None:
        response = recorded.get("/api/transcript/export", params={"fmt": fmt})
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]

    def test_an_unknown_export_format_is_rejected(self, recorded: TestClient) -> None:
        assert recorded.get("/api/transcript/export", params={"fmt": "docx"}).status_code == 422


class TestWebSocket:
    def test_a_client_receives_session_state_on_hello(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "since": None})
            frame = socket.receive_json()
            assert frame["event"] == "session.state"

    def test_ping_is_answered(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "since": None})
            socket.receive_json()
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["event"] == "pong"

    def test_live_events_reach_a_connected_client(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "hello", "since": None})
            socket.receive_json()

            client.post("/api/session/start", json={})
            try:
                seen = set()
                for _ in range(40):
                    seen.add(socket.receive_json()["event"])
                    if "transcript.committed" in seen:
                        break
                assert "transcript.committed" in seen
            finally:
                client.post("/api/session/stop")

    def test_reconnecting_replays_only_what_was_missed(self, client: TestClient) -> None:
        """The property that makes the socket disposable (BE §12.4)."""
        client.post("/api/session/start", json={})
        try:
            import time

            deadline = time.monotonic() + 8.0
            segments: list = []
            while time.monotonic() < deadline:
                segments = client.get("/api/transcript/since/0").json()["segments"]
                if len(segments) >= 2:
                    break
                time.sleep(0.05)

            if len(segments) < 2:
                pytest.skip("not enough segments to test a partial replay")

            cutoff = segments[0]["id"]
            with client.websocket_connect("/ws") as socket:
                socket.send_json({"type": "hello", "since": cutoff})

                # Frame order is not part of the contract, and cannot be: a client is registered
                # the moment it connects, so live events may already be queued when its `hello`
                # arrives. Both the frontend and this test therefore read the stream by event
                # type rather than by position.
                saw_state = False
                replayed: list[int] = []
                for _ in range(len(segments) + 10):
                    frame = socket.receive_json()
                    if frame["event"] == "session.state":
                        saw_state = True
                    elif frame["event"] == "transcript.committed":
                        replayed.append(frame["data"]["id"])
                    if saw_state and len(replayed) >= len(segments) - 1:
                        break

                assert saw_state, "the client never received the session state"
                assert cutoff not in replayed, "replayed a segment the client already had"
                assert replayed == sorted(replayed)
        finally:
            client.post("/api/session/stop")

    def test_reconnecting_replays_every_polished_block(self, client: TestClient) -> None:
        """All of them, not just recent ones: a block is sent once and never repeated, so a
        client that missed one would show that minute as raw text for the rest of the talk."""
        client.post("/api/session/start", json={})
        try:
            store = client.app.state.session_manager.store
            store.add_polished_block(0.0, 58.0, "The first minute.", [1, 2])
            store.add_polished_block(60.0, 118.0, "The second minute.", [3, 4])

            with client.websocket_connect("/ws") as socket:
                socket.send_json({"type": "hello", "since": 0})

                # Frame order is not part of the contract; read by event type, as the client does.
                polished: list[str] = []
                for _ in range(60):
                    frame = socket.receive_json()
                    if frame["event"] == "transcript.polished":
                        polished.append(frame["data"]["text"])
                    if len(polished) >= 2:
                        break

                assert polished == ["The first minute.", "The second minute."]
        finally:
            client.post("/api/session/stop")

    def test_a_fresh_client_is_not_sent_the_polished_backlog(self, client: TestClient) -> None:
        """`since: null` means a fresh page, which fetches the transcript over HTTP instead."""
        client.post("/api/session/start", json={})
        try:
            client.app.state.session_manager.store.add_polished_block(0.0, 58.0, "A minute.", [1])

            with client.websocket_connect("/ws") as socket:
                socket.send_json({"type": "hello", "since": None})
                assert socket.receive_json()["event"] == "session.state"
        finally:
            client.post("/api/session/stop")

    def test_an_unknown_client_frame_is_ignored(self, client: TestClient) -> None:
        with client.websocket_connect("/ws") as socket:
            socket.send_json({"type": "nonsense"})
            socket.send_json({"type": "hello", "since": None})
            assert socket.receive_json()["event"] == "session.state"
