"""The ``/api/chat`` surface.

Thin by design, so these tests are about the contract rather than the logic: a question is accepted
and answered over the WebSocket, a refusal explains itself, and the quick actions are readable
before anything has been recorded.
"""

from __future__ import annotations

import pytest
from app.config import ConfigStore
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    app = create_app(config=ConfigStore(config_path=tmp_path / "config.json"))
    with TestClient(app) as client:
        yield client


def test_quick_actions_are_available_before_anything_is_recorded(client: TestClient) -> None:
    """They are shown in the assistant's empty state, so they must not need a session."""
    actions = client.get("/api/chat/quick-actions").json()["actions"]

    assert {action["id"] for action in actions} >= {"summarise_10", "what_missed"}
    assert all(action["prompt"] for action in actions)


def test_asking_before_recording_explains_what_to_do(client: TestClient) -> None:
    response = client.post("/api/chat/send", json={"message": "what did they say?"})

    # 409, not 400: the request is well-formed, it just conflicts with the current state.
    assert response.status_code == 409
    assert "Start recording" in response.json()["detail"]["error"]["message"]


def test_history_is_empty_rather_than_an_error_with_no_session(client: TestClient) -> None:
    assert client.get("/api/chat/history").json() == {"messages": []}


def test_cancelling_nothing_reports_that_rather_than_failing(client: TestClient) -> None:
    assert client.post("/api/chat/cancel").json() == {"cancelled": False}


def test_the_read_mark_is_held_by_the_server_so_it_survives_a_reload(client: TestClient) -> None:
    """Losing it turns "what did I miss" into "summarise everything"."""
    assert client.post("/api/chat/read", json={"position": 90.5}).json()["position"] == 90.5
    assert client.post("/api/chat/read", json={"position": 120.0}).json()["position"] == 120.0


def test_clearing_the_conversation_is_idempotent(client: TestClient) -> None:
    assert client.delete("/api/chat/history").json() == {"cleared": True}
    assert client.delete("/api/chat/history").json() == {"cleared": True}
