"""Layer precedence, validation, and persistence for the configuration store (BE §13.1)."""

from __future__ import annotations

import json

import pytest
from app.config import LAYER_RUNTIME, LAYER_SESSION, LAYER_USER, ConfigStore, default_config
from app.config.store import deep_merge, get_in, set_in


@pytest.fixture
def store(tmp_path) -> ConfigStore:
    return ConfigStore(config_path=tmp_path / "config.json")


def test_defaults_resolve_without_any_layer(store: ConfigStore) -> None:
    config = store.resolve()
    assert config.streaming.agreement_count == 2
    assert config.vad.pause_ms == 500
    assert config.storage.retain_audio is False
    assert len(config.quick_actions) == 5


def test_later_layers_override_earlier_ones(store: ConfigStore) -> None:
    store.set("vad.sensitivity", 0.1, layer=LAYER_USER)
    assert store.resolve().vad.sensitivity == pytest.approx(0.1)

    store.set("vad.sensitivity", 0.4, layer=LAYER_SESSION)
    assert store.resolve().vad.sensitivity == pytest.approx(0.4)

    store.set("vad.sensitivity", 0.9, layer=LAYER_RUNTIME)
    assert store.resolve().vad.sensitivity == pytest.approx(0.9)


def test_clearing_a_layer_falls_back_to_the_one_beneath(store: ConfigStore) -> None:
    store.set("asr.model", "medium", layer=LAYER_USER)
    store.set("asr.model", "tiny", layer=LAYER_SESSION)
    assert store.resolve().asr.model == "tiny"

    store.clear_layer(LAYER_SESSION)
    assert store.resolve().asr.model == "medium"


def test_layers_stay_sparse_so_new_defaults_reach_existing_users(store: ConfigStore) -> None:
    store.set("vad.sensitivity", 0.2, layer=LAYER_USER)
    assert store.layer(LAYER_USER) == {"vad": {"sensitivity": 0.2}}


def test_an_invalid_value_is_rejected_and_changes_nothing(store: ConfigStore) -> None:
    store.set("streaming.agreement_count", 3)
    with pytest.raises(ValueError):
        store.set("streaming.agreement_count", 99)
    assert store.resolve().streaming.agreement_count == 3


def test_an_unknown_key_is_rejected(store: ConfigStore) -> None:
    with pytest.raises(ValueError):
        store.set("streaming.no_such_setting", 1)


def test_update_applies_several_paths_at_once(store: ConfigStore) -> None:
    store.update({"llm.mode": "api", "llm.api.model": "claude-opus-5"})
    config = store.resolve()
    assert config.llm.mode == "api"
    assert config.llm.api.model == "claude-opus-5"


def test_both_llm_configurations_persist_across_a_mode_switch(store: ConfigStore) -> None:
    """FE §7.3: flipping back must not require re-entering anything."""
    store.update({"llm.local.endpoint": "http://localhost:1234/v1", "llm.local.model": "qwen"})
    store.update({"llm.mode": "api", "llm.api.model": "claude-opus-5"})
    store.set("llm.mode", "local")

    config = store.resolve()
    assert config.llm.local.endpoint == "http://localhost:1234/v1"
    assert config.llm.api.model == "claude-opus-5"


def test_applying_a_preset_overlays_several_areas(store: ConfigStore) -> None:
    store.apply_preset("low-resource")
    config = store.resolve()
    assert config.asr.device == "cpu"
    assert config.streaming.step_s == pytest.approx(1.25)
    assert config.context.token_budget == 4000


def test_unknown_preset_raises(store: ConfigStore) -> None:
    with pytest.raises(KeyError):
        store.apply_preset("turbo")


def test_save_folds_runtime_into_user_and_writes_the_file(store: ConfigStore) -> None:
    store.set("asr.model", "medium", layer=LAYER_RUNTIME)
    store.save()

    assert store.layer(LAYER_RUNTIME) == {}
    written = json.loads(store.path.read_text(encoding="utf-8"))
    assert written == {"asr": {"model": "medium"}}


def test_load_restores_the_user_layer(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"vad": {"pause_ms": 900}}), encoding="utf-8")

    store = ConfigStore(config_path=path)
    store.load()
    assert store.resolve().vad.pause_ms == 900


def test_load_ignores_an_unreadable_file_rather_than_crashing(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")

    store = ConfigStore(config_path=path)
    store.load()
    assert store.resolve().vad.pause_ms == 500


def test_load_ignores_an_invalid_file_rather_than_crashing(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"streaming": {"agreement_count": 99}}), encoding="utf-8")

    store = ConfigStore(config_path=path)
    store.load()
    assert store.resolve().streaming.agreement_count == 2


def test_writing_to_an_unknown_layer_raises(store: ConfigStore) -> None:
    with pytest.raises(KeyError):
        store.set("vad.sensitivity", 0.5, layer="defaults")


def test_no_credential_field_exists_anywhere_in_the_schema() -> None:
    """BE §10.6: keys never enter the config file, so they must not exist in the schema."""
    serialised = json.dumps(default_config().model_dump(mode="json")).lower()
    for forbidden in ("api_key", "apikey", "credential", "secret", "token="):
        assert forbidden not in serialised


class TestMergeHelpers:
    def test_deep_merge_recurses_into_nested_dicts(self) -> None:
        base = {"a": {"b": 1, "c": 2}}
        assert deep_merge(base, {"a": {"c": 3}}) == {"a": {"b": 1, "c": 3}}

    def test_deep_merge_does_not_mutate_its_inputs(self) -> None:
        base = {"a": {"b": 1}}
        deep_merge(base, {"a": {"b": 2}})
        assert base == {"a": {"b": 1}}

    def test_lists_replace_rather_than_merge(self) -> None:
        assert deep_merge({"xs": [1, 2, 3]}, {"xs": [9]}) == {"xs": [9]}

    def test_set_in_creates_intermediate_dicts(self) -> None:
        target: dict = {}
        set_in(target, "a.b.c", 7)
        assert target == {"a": {"b": {"c": 7}}}

    def test_get_in_returns_none_for_a_missing_path(self) -> None:
        assert get_in({"a": {"b": 1}}, "a.z.q") is None
