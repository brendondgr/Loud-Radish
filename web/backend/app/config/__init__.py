"""Configuration system — layers, presets, hot-swap classification, and credentials.

See ``docs/plans/live-seminar-transcriber.md`` Step 1 and BE §13 for the design.
"""

from .credentials import CredentialStore, CredentialUnavailableError
from .defaults import DEFAULT_QUICK_ACTIONS, default_config, default_layer
from .hotswap import CLASS_CONSEQUENCE, HotSwapClass, classify, classify_many
from .presets import PRESET_DESCRIPTIONS, PRESETS, preset_names, preset_overlay
from .schema import (
    AppConfig,
    AsrConfig,
    AudioConfig,
    CaptureConfig,
    ContextConfig,
    LlmConfig,
    PolishConfig,
    QuickAction,
    RecordingConfig,
    ShortcutsConfig,
    StorageConfig,
    StreamingConfig,
    VadConfig,
)
from .store import (
    LAYER_RUNTIME,
    LAYER_SESSION,
    LAYER_USER,
    ConfigStore,
    default_config_path,
)

__all__ = [
    "CLASS_CONSEQUENCE",
    "DEFAULT_QUICK_ACTIONS",
    "LAYER_RUNTIME",
    "LAYER_SESSION",
    "LAYER_USER",
    "PRESETS",
    "PRESET_DESCRIPTIONS",
    "AppConfig",
    "AsrConfig",
    "AudioConfig",
    "CaptureConfig",
    "ConfigStore",
    "ContextConfig",
    "CredentialStore",
    "CredentialUnavailableError",
    "HotSwapClass",
    "LlmConfig",
    "PolishConfig",
    "QuickAction",
    "RecordingConfig",
    "ShortcutsConfig",
    "StorageConfig",
    "StreamingConfig",
    "VadConfig",
    "classify",
    "classify_many",
    "default_config",
    "default_config_path",
    "default_layer",
    "preset_names",
    "preset_overlay",
]
