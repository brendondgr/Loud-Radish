"""Hot-swap classification — what changing a setting costs the running session (BE §13.3)."""

from __future__ import annotations

import pytest
from app.config import CLASS_CONSEQUENCE, ConfigStore, HotSwapClass, classify, classify_many


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("vad.sensitivity", HotSwapClass.LIVE),
        ("vad.pause_ms", HotSwapClass.LIVE),
        ("audio.gain_db", HotSwapClass.LIVE),
        ("audio.normalise_gain", HotSwapClass.LIVE),
        ("llm.generation.temperature", HotSwapClass.LIVE),
        ("context.token_budget", HotSwapClass.LIVE),
        ("asr.session_prompt", HotSwapClass.LIVE),
        ("asr.model", HotSwapClass.RESTART_STAGE),
        ("asr.backend", HotSwapClass.RESTART_STAGE),
        ("asr.device", HotSwapClass.RESTART_STAGE),
        ("audio.device_id", HotSwapClass.RESTART_STAGE),
        ("audio.source_type", HotSwapClass.RESTART_STAGE),
        ("vad.detector", HotSwapClass.RESTART_STAGE),
        ("storage.session_dir", HotSwapClass.RESTART_SESSION),
        ("storage.retain_audio", HotSwapClass.RESTART_SESSION),
    ],
)
def test_paths_classify_as_documented(path: str, expected: HotSwapClass) -> None:
    assert classify(path) is expected


def test_unknown_paths_default_to_live() -> None:
    """The safe direction: a wrongly-live setting is correctable, a wrongly-blocking one is not."""
    assert classify("something.entirely.new") is HotSwapClass.LIVE


def test_a_nested_path_inherits_its_prefix_classification() -> None:
    assert classify("storage.session_dir.subkey") is HotSwapClass.RESTART_SESSION


def test_classify_many_returns_the_most_disruptive_class() -> None:
    assert classify_many(["vad.sensitivity", "asr.model"]) is HotSwapClass.RESTART_STAGE
    assert classify_many(["asr.model", "storage.session_dir"]) is HotSwapClass.RESTART_SESSION
    assert classify_many(["vad.sensitivity", "context.token_budget"]) is HotSwapClass.LIVE


def test_classify_many_of_nothing_is_live() -> None:
    assert classify_many([]) is HotSwapClass.LIVE


def test_every_class_has_a_user_facing_consequence() -> None:
    """FE reads these strings to warn the user; a missing one would render an empty warning."""
    for member in HotSwapClass:
        assert CLASS_CONSEQUENCE[member].strip()


def test_the_store_reports_the_class_of_a_change(tmp_path) -> None:
    store = ConfigStore(config_path=tmp_path / "config.json")
    assert store.set("vad.sensitivity", 0.4) is HotSwapClass.LIVE
    assert store.set("asr.model", "medium") is HotSwapClass.RESTART_STAGE
    assert store.set("storage.session_dir", "/tmp/sessions") is HotSwapClass.RESTART_SESSION


def test_a_multi_path_update_reports_the_worst_class(tmp_path) -> None:
    store = ConfigStore(config_path=tmp_path / "config.json")
    result = store.update({"vad.sensitivity": 0.4, "asr.precision": "float16"})
    assert result is HotSwapClass.RESTART_STAGE


def test_applying_a_preset_reports_a_stage_restart(tmp_path) -> None:
    """Presets change the ASR model, so the frontend must warn about the transcription gap."""
    store = ConfigStore(config_path=tmp_path / "config.json")
    assert store.apply_preset("accuracy") is HotSwapClass.RESTART_STAGE
