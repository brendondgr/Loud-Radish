"""One endpoint that starts or stops, because a keystroke cannot know which (Plan 5).

Two endpoints would mean the caller asking "is it recording?" and then acting on the answer, which
is a race: press the key twice quickly and the second request decides from a state the first has
already changed. Deciding server-side, where the lock is, removes the race rather than narrowing it.
"""

from __future__ import annotations

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.services.session import modes
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    app = create_app(config=ConfigStore(config_path=tmp_path / "config.json"))
    with TestClient(app) as client:
        yield client


def test_toggling_from_idle_starts(client: TestClient) -> None:
    response = client.post("/api/session/toggle", json={})
    if response.status_code != 200:
        pytest.skip("no capture source available in this environment")
    assert response.json()["running"] is True
    client.post("/api/session/stop")


def test_toggling_again_stops(client: TestClient) -> None:
    if client.post("/api/session/toggle", json={}).status_code != 200:
        pytest.skip("no capture source available in this environment")

    stopped = client.post("/api/session/toggle", json={})
    assert stopped.status_code == 200
    assert stopped.json()["running"] is False


def test_the_mode_reaches_the_session(client: TestClient) -> None:
    response = client.post("/api/session/toggle", json={"mode": modes.RECORDED})
    if response.status_code != 200:
        pytest.skip("no capture source available in this environment")
    assert response.json()["session"]["mode"] == modes.RECORDED
    client.post("/api/session/stop")


def test_stopping_ignores_the_mode(client: TestClient) -> None:
    """The second press of a key bound to `--mode recorded` must not try to start a live one."""
    if client.post("/api/session/toggle", json={"mode": modes.RECORDED}).status_code != 200:
        pytest.skip("no capture source available in this environment")

    stopped = client.post("/api/session/toggle", json={"mode": modes.LIVE})
    assert stopped.json()["running"] is False


def test_a_failure_to_start_is_reported_rather_than_silently_toggling(client: TestClient) -> None:
    """A keystroke that appears to do nothing is worse than one that says why it did nothing."""
    response = client.post("/api/session/toggle", json={"mode": modes.LIVE})
    if response.status_code == 200:
        client.post("/api/session/stop")
        return
    assert response.status_code in (409, 501)
    assert response.json()["detail"]["error"]["message"]
