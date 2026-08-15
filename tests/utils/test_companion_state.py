"""What the companion makes of the server's state, including its absence (Plan 5).

The companion is a **remote control**: it polls, it holds no state of its own, and it can be killed
and restarted at any moment without the server noticing. That is the property that makes a
background helper safe to ship, and these tests are mostly about the two ends of it — that an
unreachable server is an *answer* rather than a crash, and that the picture follows the mode as well
as the run state.
"""

from __future__ import annotations

import json
import urllib.error

import pytest
from app.companion import main as companion_main
from app.companion.animation import FrameClock, Snapshot
from app.companion.visual_states import SERVER_DOWN


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def server(monkeypatch):
    """Answer `GET /api/session` with a scripted payload, or refuse."""
    state: dict = {"payload": {"running": False, "session": None}}

    def fake_open(request, timeout=None):  # noqa: ANN001, ARG001
        payload = state["payload"]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload)

    monkeypatch.setattr(companion_main.urllib.request, "urlopen", fake_open)
    return state


def test_an_unreachable_server_is_an_answer_not_a_crash(server) -> None:
    """A tray icon that looks fine while the thing behind it is dead is worse than no icon."""
    server["payload"] = urllib.error.URLError("connection refused")
    snapshot = companion_main.read_state("http://127.0.0.1:8395")

    assert snapshot.reachable is False
    assert snapshot.visual().name == SERVER_DOWN


def test_nonsense_from_the_server_is_also_survivable(server) -> None:
    server["payload"] = ValueError("not json")
    assert companion_main.read_state("http://x").reachable is False


def test_an_idle_server_reads_as_idle(server) -> None:
    server["payload"] = {"running": False, "session": None}
    assert companion_main.read_state("http://x").visual().name == "idle"


def test_the_picture_follows_the_mode_as_well_as_the_state(server) -> None:
    """The finding that shaped this step: two modes, one run state, two pictures."""
    server["payload"] = {"running": True, "session": {"mode": "live"}}
    live = companion_main.read_state("http://x").visual()

    server["payload"] = {"running": True, "session": {"mode": "recorded"}}
    recorded = companion_main.read_state("http://x").visual()

    assert live.name != recorded.name


def test_a_transcription_pass_shows_even_with_no_session(server) -> None:
    """It outlives the session by up to half an hour, and "idle" would be a lie."""
    server["payload"] = {
        "running": False,
        "session": None,
        "transcription": {"state": "running", "progress": 0.3},
    }
    assert companion_main.read_state("http://x").visual().name == "transcribing"


# -- the frame clock ---------------------------------------------------------------------


def test_the_clock_reports_when_the_picture_changes() -> None:
    frames: list = []
    clock = FrameClock(lambda frame, visual: frames.append(visual.name))

    assert clock.update(Snapshot(reachable=True, state="recording", mode="live")) is True
    # The same picture again is not a change, even though the snapshot object differs.
    assert clock.update(Snapshot(reachable=True, state="recording", mode="live")) is False


def test_drawing_produces_a_frame_for_the_current_state() -> None:
    seen: list = []
    clock = FrameClock(lambda frame, visual: seen.append(visual.name))
    clock.update(Snapshot(reachable=True, state="recording", mode="recorded"))
    clock.draw_once()

    assert seen == ["recording"]


def test_holding_still_renders_the_same_frame_every_time() -> None:
    """There is no prefers-reduced-motion in a tray and no way to ask for one, so this is it."""
    clock = FrameClock(lambda frame, visual: None, hold_still=True)
    clock.update(Snapshot(reachable=True, state="recording", mode="live"))
    assert clock.draw_once().svg == clock.draw_once().svg


def test_animating_does_not(monkeypatch) -> None:
    clock = FrameClock(lambda frame, visual: None)
    clock.update(Snapshot(reachable=True, state="recording", mode="live"))
    first = clock.draw_once().svg

    import time

    time.sleep(0.15)
    assert clock.draw_once().svg != first


def test_stopping_a_clock_that_never_started_is_harmless() -> None:
    FrameClock(lambda frame, visual: None).stop()


def test_the_companion_toggles_its_own_listening_switch() -> None:
    """Independent of recording: disabling shortcuts must not stop a talk being recorded."""
    companion = companion_main.Companion()
    assert companion.listening is True
    companion.activate("listening")
    assert companion.listening is False
    companion.activate("listening")
    assert companion.listening is True


def test_an_unknown_menu_item_is_reported_rather_than_raising() -> None:
    assert "unknown" in companion_main.Companion().activate("nonsense")
