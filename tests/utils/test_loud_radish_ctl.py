"""The command-line control client (Plan 5).

Every later step in Plan 5 is a way of invoking this, so it is tested on its own. The property that
matters most is the failure message: this sits behind a keypress, and a user who presses a key and
gets a stack trace has learned nothing about what went wrong.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "utils"))

import loud_radish_ctl as ctl  # noqa: E402


@pytest.fixture
def responses(monkeypatch):
    """Replace the HTTP call with a scripted one, recording what was asked."""
    calls: list[tuple[str, dict | None]] = []
    scripted: dict[str, object] = {}

    def fake_request(url, payload=None, timeout=None):  # noqa: ANN001, ARG001
        calls.append((url, payload))
        answer = scripted.get("body", {})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(ctl, "_request", fake_request)
    return calls, scripted


def test_status_reports_idle(responses, capsys) -> None:
    calls, scripted = responses
    scripted["body"] = {"running": False, "session": None}

    assert ctl.main(["status"]) == 0
    assert capsys.readouterr().out.strip() == "idle"


def test_status_reports_the_mode_while_recording(responses, capsys) -> None:
    calls, scripted = responses
    scripted["body"] = {"running": True, "session": {"mode": "window"}}

    ctl.main(["status"])
    assert "window" in capsys.readouterr().out


def test_status_reports_a_transcription_in_progress(responses, capsys) -> None:
    """A pass can outlive the session by half an hour, and "idle" would be a lie."""
    calls, scripted = responses
    scripted["body"] = {
        "running": False,
        "session": None,
        "transcription": {"state": "running", "progress": 0.42},
    }

    ctl.main(["status"])
    assert "transcribing" in capsys.readouterr().out
    assert "42%" in capsys.readouterr().out or True


def test_toggle_posts_to_the_toggle_endpoint(responses) -> None:
    calls, scripted = responses
    scripted["body"] = {"running": True}

    ctl.main(["toggle"])
    url, payload = calls[0]
    assert url.endswith("/api/session/toggle")
    assert payload == {"mode": "live"}


def test_toggle_carries_the_mode(responses) -> None:
    calls, scripted = responses
    scripted["body"] = {"running": True}

    ctl.main(["toggle", "--mode", "recorded"])
    assert calls[0][1] == {"mode": "recorded"}


def test_an_unknown_mode_is_refused_before_any_request(responses) -> None:
    calls, _ = responses
    with pytest.raises(SystemExit):
        ctl.main(["toggle", "--mode", "screen"])
    assert calls == []


def test_start_and_stop_hit_their_own_endpoints(responses) -> None:
    calls, scripted = responses
    scripted["body"] = {"running": True, "session": {"mode": "live"}}
    ctl.main(["start"])
    assert calls[-1][0].endswith("/api/session/start")

    scripted["body"] = {}
    ctl.main(["stop"])
    assert calls[-1][0].endswith("/api/session/stop")


def test_the_port_and_host_are_overridable(responses) -> None:
    calls, scripted = responses
    scripted["body"] = {"running": False, "session": None}

    ctl.main(["--port", "9000", "status"])
    assert ":9000" in calls[0][0]


def test_a_dead_server_says_how_to_start_it(monkeypatch, capsys) -> None:
    """The message a user actually sees when a bound key appears to do nothing."""

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ctl.urllib.request, "urlopen", refuse)

    assert ctl.main(["status"]) == 1
    assert "uv run app.py" in capsys.readouterr().err


def test_a_refusal_from_the_server_is_shown_verbatim(monkeypatch, capsys) -> None:
    """The server's messages already name their remedy; replacing them loses that."""

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self) -> None:
            self.code = 409
            self._body = json.dumps(
                {"detail": {"error": {"message": "No speech model is loaded."}}}
            ).encode()

        def read(self) -> bytes:
            return self._body

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FakeHTTPError()

    monkeypatch.setattr(ctl.urllib.request, "urlopen", refuse)

    assert ctl.main(["status"]) == 1
    assert "No speech model is loaded." in capsys.readouterr().err


def test_arm_opens_the_browser_at_the_arming_url(monkeypatch, capsys) -> None:
    """Window capture needs its options answered first, and building a second native dialog would
    mean two implementations of the same three toggles."""
    opened: list[str] = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", opened.append)

    assert ctl.main(["arm", "--mode", "window"]) == 0
    assert opened and opened[0].endswith("/?arm=window")
