"""Which settings survive a restart, and which are meant not to (D-046).

The reported fault: choose a microphone, use it, restart, and the application is listening to
something else. Every write from the settings interface went to the **runtime** layer, which exists
precisely so that a threshold can be nudged during a talk and abandoned by restarting — and the Save
button is what commits it. That is right for a threshold and wrong for a microphone: a device is an
identity, and the symptom of losing it does not appear until the next recording is made with the
wrong input, or with none.

So a small set of paths is written straight through. These tests pin both halves — that the device
persists without anyone pressing Save, and that a threshold still does not.
"""

from __future__ import annotations

import json

import pytest
from app.config import ConfigStore
from app.config.store import PERSISTENT_PATHS
from fastapi.testclient import TestClient


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "loud-radish-config.json"


@pytest.fixture
def client(config_path):
    from app.main import create_app

    store = ConfigStore(config_path=config_path)
    store.load()
    with TestClient(create_app(config=store)) as test_client:
        yield test_client


def _on_disk(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# -- what survives ---------------------------------------------------------------------------


def test_choosing_a_microphone_reaches_the_file_without_pressing_save(client, config_path) -> None:
    """The reported fault, as a test."""
    response = client.patch(
        "/api/config", json={"changes": {"audio.device_id": "samson-gomic-usb-audio-hw-3-0"}}
    )

    assert response.status_code == 200
    assert _on_disk(config_path)["audio"]["device_id"] == "samson-gomic-usb-audio-hw-3-0"


def test_the_choice_is_still_there_for_a_store_that_starts_cold(client, config_path) -> None:
    """A restart is a new `ConfigStore` reading the same file, which is what this builds."""
    client.patch(
        "/api/config", json={"changes": {"audio.device_id": "samson-gomic-usb-audio-hw-3-0"}}
    )

    restarted = ConfigStore(config_path=config_path)
    restarted.load()

    assert restarted.resolve().audio.device_id == "samson-gomic-usb-audio-hw-3-0"


def test_the_source_type_travels_with_the_device(client, config_path) -> None:
    """Choosing a file and choosing a microphone are the same act, and both must survive."""
    client.patch(
        "/api/config",
        json={"changes": {"audio.source_type": "microphone", "audio.device_id": "a-microphone"}},
    )

    stored = _on_disk(config_path)["audio"]
    assert stored["source_type"] == "microphone"
    assert stored["device_id"] == "a-microphone"


# -- what deliberately does not ----------------------------------------------------------------


def test_a_threshold_still_waits_for_save(client, config_path) -> None:
    """The Save button keeps its job. Persisting everything would remove the ability to try a
    setting during a talk and get rid of it by restarting."""
    response = client.patch("/api/config", json={"changes": {"vad.sensitivity": 0.9}})

    assert response.status_code == 200
    assert response.json()["config"]["vad"]["sensitivity"] == 0.9
    assert "vad" not in _on_disk(config_path)


def test_persisting_a_device_does_not_drag_an_experiment_to_disk(client, config_path) -> None:
    """**The reason `persist` is not `update(layer="user")` followed by `save()`.** `save` folds the
    whole runtime layer down, so committing a microphone that way would also commit every unrelated
    slider the user had been playing with in the same session."""
    client.patch("/api/config", json={"changes": {"vad.sensitivity": 0.9}})

    client.patch("/api/config", json={"changes": {"audio.device_id": "a-microphone"}})

    stored = _on_disk(config_path)
    assert stored["audio"]["device_id"] == "a-microphone"
    assert "vad" not in stored, "an unsaved experiment reached the config file"


def test_a_later_runtime_write_does_not_shadow_the_persisted_device(client, config_path) -> None:
    """Runtime outranks user in the merge, so a stale runtime value would win over what was just
    written and the setting would appear not to have taken until a restart."""
    client.patch(
        "/api/config",
        json={"changes": {"audio.device_id": "an-old-microphone"}, "layer": "runtime"},
    )

    client.patch("/api/config", json={"changes": {"audio.device_id": "the-new-microphone"}})

    assert client.get("/api/config").json()["config"]["audio"]["device_id"] == "the-new-microphone"


def test_the_persistent_set_stays_small(client) -> None:
    """A guard, not a tautology. Persisting everything by accident would silently retire the Save
    button and make every experiment permanent."""
    assert PERSISTENT_PATHS <= {"audio.source_type", "audio.device_id", "audio.file_path"}
