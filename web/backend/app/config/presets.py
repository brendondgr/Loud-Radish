"""Named configuration profiles (BE §13.4).

Three profiles rather than twenty tunable parameters. Each is a sparse overlay applied on top of the
defaults; keys absent from a preset keep whatever the layer beneath them resolved to.
"""

from __future__ import annotations

from typing import Any

PRESET_ACCURACY = "accuracy"
PRESET_BALANCED = "balanced"
PRESET_LOW_RESOURCE = "low-resource"

PRESETS: dict[str, dict[str, Any]] = {
    PRESET_ACCURACY: {
        "asr": {"model": "medium", "precision": "float16", "beam_size": 5},
        "streaming": {
            "agreement_count": 3,
            "step_s": 1.0,
            "max_buffer_s": 28.0,
            "retained_context_s": 1.0,
        },
        "vad": {"sensitivity": 0.5},
    },
    PRESET_BALANCED: {
        "asr": {"model": "small", "precision": "int8", "beam_size": 1},
        "streaming": {
            "agreement_count": 2,
            "step_s": 0.75,
            "max_buffer_s": 25.0,
            "retained_context_s": 0.75,
        },
        "vad": {"sensitivity": 0.6},
    },
    PRESET_LOW_RESOURCE: {
        "asr": {"model": "base", "device": "cpu", "precision": "int8", "beam_size": 1},
        "streaming": {
            "agreement_count": 2,
            "step_s": 1.25,
            "max_buffer_s": 18.0,
            "retained_context_s": 0.5,
        },
        "vad": {"enabled": True, "sensitivity": 0.75},
        "context": {"summary_interval_s": 600.0, "token_budget": 4000},
    },
}

PRESET_DESCRIPTIONS: dict[str, str] = {
    PRESET_ACCURACY: "Larger model, longer buffer, stricter agreement. Slower, more accurate.",
    PRESET_BALANCED: "The default. Small model, two-pass agreement, moderate buffer.",
    PRESET_LOW_RESOURCE: "Small model on CPU with aggressive silence gating. Lowest compute cost.",
}


def preset_names() -> list[str]:
    """Return the available preset names in presentation order."""
    return [PRESET_ACCURACY, PRESET_BALANCED, PRESET_LOW_RESOURCE]


def preset_overlay(name: str) -> dict[str, Any]:
    """Return the sparse overlay for ``name``.

    Raises:
        KeyError: if ``name`` is not a known preset.
    """
    if name not in PRESETS:
        raise KeyError(f"Unknown preset {name!r}. Available presets: {', '.join(preset_names())}")
    return PRESETS[name]
