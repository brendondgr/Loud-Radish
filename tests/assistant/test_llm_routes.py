"""The ``/api/llm`` surface.

The tests that matter most here are the negative ones. A credential must never come back out, and a
connection test must never answer with generic failure text — both are properties that only stay
true if something checks them.
"""

from __future__ import annotations

import httpx
import pytest
from app.config import ConfigStore
from app.main import create_app
from app.services.llm import openai_compatible
from fastapi.testclient import TestClient


@pytest.fixture
def store(tmp_path) -> ConfigStore:
    return ConfigStore(config_path=tmp_path / "config.json")


@pytest.fixture
def client(store: ConfigStore):
    app = create_app(config=store)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def offline(monkeypatch):
    """Make every outbound request fail as if nothing were listening.

    Without this the tests would reach whatever happens to be running on the developer's machine,
    which is the difference between a test suite and a coin flip.
    """

    class Refused(httpx.AsyncClient):
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

        def stream(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", Refused)


# -- configuration ---------------------------------------------------------------------


def test_config_returns_both_modes_so_switching_back_re_enters_nothing(client: TestClient) -> None:
    body = client.get("/api/llm/config").json()

    assert body["mode"] == "local"
    assert "endpoint" in body["local"]
    assert "provider" in body["api"]
    assert body["local"]["presets"], "the local tab needs suggested addresses"
    assert body["api"]["providers"], "the API tab needs a provider list"


def test_config_never_carries_a_credential_value(client: TestClient, monkeypatch) -> None:
    """Store a real secret, then check it does not come back.

    Asserted against a stored value rather than by scanning for the string "api_key": the response
    legitimately names the *environment variables* a provider reads, and a test that cannot tell a
    variable's name from its value would have to be weakened until it caught nothing.
    """
    fake = _keyring({"local": "sk-secret"})
    monkeypatch.setattr("app.config.credentials._keyring", lambda: fake)

    body = client.get("/api/llm/config")

    assert "sk-secret" not in body.text
    # Presence only — that is the single credential fact the frontend is allowed to learn.
    assert body.json()["credential"] == {
        "provider": "local",
        "present": True,
        "source": "os-credential-store",
        "writable": True,
    }


def test_the_status_endpoint_makes_no_network_call(client: TestClient, offline) -> None:
    """The header reads this on every page load, so it must not wait on a model server."""
    body = client.get("/api/llm/status").json()

    assert body["configured"] is False  # no model chosen out of the box
    assert body["mode"] == "local"
    assert body["local"] is True
    assert "Settings" in body["note"]


def test_status_reports_configured_once_a_model_is_chosen(client: TestClient) -> None:
    client.patch("/api/config", json={"changes": {"llm.local.model": "default-model"}})
    body = client.get("/api/llm/status").json()

    assert body["configured"] is True
    assert body["model"] == "default-model"


def test_an_api_provider_is_not_reported_as_local(client: TestClient) -> None:
    """This drives the privacy indicator: claiming local when data leaves would be a real harm."""
    client.patch(
        "/api/config",
        json={"changes": {"llm.mode": "api", "llm.api.provider": "anthropic"}},
    )
    assert client.get("/api/llm/status").json()["local"] is False


# -- connection testing ------------------------------------------------------------------


def test_a_dead_server_is_reported_as_no_server_with_the_address(
    client: TestClient, offline
) -> None:
    body = client.post("/api/llm/test", json={}).json()

    assert body["result"] == "no_server"
    assert "11434" in body["message"]  # the default endpoint, named in the remedy
    assert body["models"] == []


def test_test_probes_the_address_on_screen_not_the_saved_one(
    client: TestClient, monkeypatch
) -> None:
    """Otherwise the button is useless exactly when it is needed — while typing a new address."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "default-model"}]})

    _use_mock_transport(monkeypatch, handler)

    body = client.post(
        "/api/llm/test",
        json={"local": {"endpoint": "http://localhost:9090/v1", "model": "default-model"}},
    ).json()

    assert body["result"] == "connected"
    assert seen == ["http://localhost:9090/v1/models"]
    # And the probe must not have written anything.
    assert client.get("/api/llm/config").json()["local"]["endpoint"] != "http://localhost:9090/v1"


def test_test_ignores_decorative_keys_the_form_echoes_back(client: TestClient, monkeypatch) -> None:
    """``GET /api/llm/config`` adds ``presets`` and ``providers``; round-tripping must not 422."""
    _use_mock_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"data": [{"id": "x"}]})
    )

    response = client.post(
        "/api/llm/test",
        json={
            "mode": "local",
            "local": {"endpoint": "http://localhost:9090/v1", "presets": [{"name": "Ollama"}]},
            "api": {"provider": "anthropic", "providers": []},
        },
    )
    assert response.status_code == 200


def test_an_invalid_value_is_a_422_naming_the_field(client: TestClient) -> None:
    response = client.post("/api/llm/test", json={"mode": "sideways"})

    assert response.status_code == 422
    assert "not valid" in response.json()["detail"]["error"]["message"]


def test_the_model_list_degrades_to_an_explanation_rather_than_an_error(
    client: TestClient, offline
) -> None:
    """It populates a dropdown while the user is still typing; a red error would be noise."""
    response = client.get("/api/llm/models")

    assert response.status_code == 200
    assert response.json()["models"] == []
    assert "No server responded" in response.json()["note"]


def test_listing_models_uses_the_address_on_screen_and_ignores_the_saved_model(
    client: TestClient, monkeypatch
) -> None:
    """The settings form's Get button (D-067). The connection test refuses when the saved model
    is not among what the address offers — right for a test, and the reported pop-up for a button
    whose job is to find out what can be chosen."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "gemma-4-26B-it"}, {"id": "nemotron"}]})

    _use_mock_transport(monkeypatch, handler)
    client.patch("/api/config", json={"changes": {"llm.local.model": "default-model"}})

    body = client.post(
        "/api/llm/models", json={"local": {"endpoint": "http://localhost:7070/v1"}}
    ).json()

    assert [model["id"] for model in body["models"]] == ["gemma-4-26B-it", "nemotron"]
    assert body["endpoint"] == "http://localhost:7070/v1"
    assert seen == ["http://localhost:7070/v1/models"]
    # For comparison: the test *does* refuse, naming the model, which is its job.
    verdict = client.post(
        "/api/llm/test", json={"local": {"endpoint": "http://localhost:7070/v1"}}
    ).json()
    assert verdict["result"] != "connected"
    assert "default-model" in verdict["message"]


# -- credentials -------------------------------------------------------------------------


def test_storing_a_credential_reports_honestly_when_it_cannot_persist(
    client: TestClient, monkeypatch
) -> None:
    """A key the user believes is saved but is not causes an auth failure much later."""
    monkeypatch.setattr("app.config.credentials._keyring", lambda: None)

    response = client.put("/api/llm/credential", json={"provider": "anthropic", "value": "sk-x"})

    assert response.status_code == 409
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]["error"]["message"]


def test_a_stored_credential_is_never_echoed_back(client: TestClient, monkeypatch) -> None:
    # Built once: a factory called per lookup would forget every write between calls.
    fake = _keyring()
    monkeypatch.setattr("app.config.credentials._keyring", lambda: fake)

    response = client.put("/api/llm/credential", json={"provider": "anthropic", "value": "sk-abc"})

    assert response.status_code == 200
    assert "sk-abc" not in response.text
    assert response.json() == {
        "provider": "anthropic",
        "present": True,
        "source": "os-credential-store",
        "writable": True,
    }

    assert client.delete("/api/llm/credential/anthropic").json()["present"] is False


def _keyring(initial: dict[str, str] | None = None):
    """An in-memory stand-in for the OS credential store."""
    stored = dict(initial or {})

    class FakeKeyring:
        @staticmethod
        def set_password(service, provider, value) -> None:
            stored[provider] = value

        @staticmethod
        def get_password(service, provider):
            return stored.get(provider)

        @staticmethod
        def delete_password(service, provider) -> None:
            stored.pop(provider, None)

    return FakeKeyring


def _use_mock_transport(monkeypatch, handler) -> None:
    """Point the OpenAI-compatible client's transport at ``handler`` instead of the network."""
    real = httpx.AsyncClient

    def build(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(openai_compatible.httpx, "AsyncClient", build)
