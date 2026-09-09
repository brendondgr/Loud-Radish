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
from app.config.store import PERSISTENT_PATHS, persists
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
    button and make every experiment permanent.

    `polish.instructions` earns its place on the same grounds as the microphone: it holds prose
    somebody sat and wrote, and losing that to a restart reads as the application discarding their
    work. The numeric polish settings beside it deliberately do not qualify."""
    assert PERSISTENT_PATHS <= {
        "audio.source_type",
        "audio.device_id",
        "audio.file_path",
        "polish.instructions",
    }


def test_rewrite_instructions_reach_the_file_without_saving(client, config_path) -> None:
    """Written with Done rather than Save, and still there on the next start."""
    client.patch("/api/config", json={"changes": {"polish.instructions": "Keep every hesitation."}})

    stored = _on_disk(config_path)
    assert stored["polish"]["instructions"] == "Keep every hesitation."
    assert "chunk_seconds" not in stored.get("polish", {}), "a tuning value reached the file"


def test_the_settings_dropdown_endpoint_also_persists(client, config_path) -> None:
    """**The route the fault was actually reported against.** Settings -> Audio calls
    `POST /api/audio/device`, not `PATCH /api/config`, so fixing the patch route alone left the
    dropdown reverting on every restart exactly as before."""
    response = client.post(
        "/api/audio/device",
        json={"source_type": "microphone", "device_id": "samson-gomic-usb-audio-hw-3-0"},
    )

    assert response.status_code == 200
    stored = _on_disk(config_path)["audio"]
    assert stored["device_id"] == "samson-gomic-usb-audio-hw-3-0"
    assert stored["source_type"] == "microphone"


def test_choosing_a_file_source_from_the_dropdown_persists_its_path(client, config_path) -> None:
    client.post(
        "/api/audio/device",
        json={"source_type": "file", "device_id": None, "file_path": "/tmp/a.wav"},
    )

    assert _on_disk(config_path)["audio"]["file_path"] == "/tmp/a.wav"


# -- the settings window has no Save button, so everything it writes must survive ----------------


def test_a_rebound_shortcut_survives_a_restart(client, config_path) -> None:
    """**The reported fault, and the second time this exact mistake was made.** A shortcut set in
    the native settings window reverted to the shipped default on the next start — so the user
    pressed their own key and nothing happened. Same shape as the microphone: a deliberate choice,
    lost silently, noticed much later."""
    client.patch("/api/config", json={"changes": {"shortcuts.dictate": "Meta+Shift+Space"}})

    restarted = ConfigStore(config_path=config_path)
    restarted.load()

    assert restarted.resolve().shortcuts.dictate == "Meta+Shift+Space"


def test_the_dictation_options_survive_a_restart(client, config_path) -> None:
    """The same window writes these, and it has no Save button either."""
    client.patch(
        "/api/config",
        json={"changes": {"dictation.paste_chord": "ctrl+shift+v", "dictation.cleanup": "off"}},
    )

    restarted = ConfigStore(config_path=config_path)
    restarted.load()

    assert restarted.resolve().dictation.paste_chord == "ctrl+shift+v"
    assert restarted.resolve().dictation.cleanup == "off"


def test_whole_families_persist_not_just_named_paths() -> None:
    """A shortcut that did not exist when this was written must persist too, or adding one
    reintroduces the fault."""
    assert persists("shortcuts.some_future_action")
    assert persists("dictation.some_future_option")
    assert persists("audio.device_id")


def test_the_tuning_values_still_wait_for_save() -> None:
    """The Save button keeps its job for the web panel's thresholds — the settings someone tries
    mid-talk and gets rid of by restarting."""
    assert not persists("vad.sensitivity")
    assert not persists("polish.min_retained_ratio")
    assert not persists("asr.beam_size")
    assert not persists("streaming.step_s")


# -- settings that no longer exist ------------------------------------------------------------


def test_a_retired_setting_is_dropped_rather_than_the_whole_file_ignored(config_path) -> None:
    """The schema forbids unknown keys, and a rejected file is ignored *whole* — so retiring one
    setting would have silently reset every setting the user ever chose (D-062)."""
    config_path.write_text(
        json.dumps({"recording": {"batch_overlap_s": 1.0, "batch_window_s": 45.0}}),
        encoding="utf-8",
    )
    store = ConfigStore(config_path=config_path)
    store.load()

    assert store.resolve().recording.batch_window_s == 45.0
    assert "batch_overlap_s" not in store.resolve().recording.model_dump()


def test_the_shipped_instructions_come_back_with_the_configuration(client) -> None:
    """The panel shows the shipped text in an empty field, so it has to be served rather than
    copied into the frontend — a paragraph with two sources of truth drifts on the first edit."""
    from app.services.dictation.prompts import DEFAULT_DICTATION_PROMPT
    from app.services.polish.prompts import DEFAULT_POLISH_PROMPT

    defaults = client.get("/api/config").json()["prompt_defaults"]

    assert defaults["polish.instructions"] == DEFAULT_POLISH_PROMPT
    assert defaults["dictation.instructions"] == DEFAULT_DICTATION_PROMPT


def test_a_write_carries_the_shipped_instructions_too(client) -> None:
    """A client that re-renders from the patch response must not have to fetch them separately."""
    response = client.patch("/api/config", json={"changes": {"polish.enabled": True}})

    assert response.json()["prompt_defaults"]["polish.instructions"].startswith("You are turning")
