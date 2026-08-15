"""The health endpoint, and that the application factory builds a usable app."""

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


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_which_optional_groups_are_installed(client: TestClient) -> None:
    """A missing model backend should surface here, not as a failure when the user hits record."""
    optional = client.get("/api/health").json()["optional"]
    assert set(optional) == {
        "asr_whisper",
        "audio_device",
        "vad_silero",
        "credentials",
        # Not a dependency group but reported alongside them, because it is the same question from
        # the user's side: can this machine do the thing, and if not what is missing (D-020).
        "window_capture",
    }
    assert all(isinstance(value, bool) for value in optional.values())


def test_health_never_returns_credential_material(client: TestClient) -> None:
    body = client.get("/api/health").text.lower()
    assert "api_key" not in body
    assert "sk-" not in body


def test_the_app_exposes_its_config_store(tmp_path) -> None:
    store = ConfigStore(config_path=tmp_path / "config.json")
    app = create_app(config=store)
    assert app.state.config is store
    assert app.state.credentials is not None


def test_lifespan_loads_the_config_file_on_startup(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"vad": {"pause_ms": 750}}', encoding="utf-8")

    app = create_app(config=ConfigStore(config_path=path))
    with TestClient(app):
        assert app.state.config.resolve().vad.pause_ms == 750
