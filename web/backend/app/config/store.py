"""Layered configuration resolution and persistence (BE §13.1).

Four layers, resolved in order, each overriding the one before it:

1. built-in defaults      — ``defaults.default_layer()``
2. user config file       — JSON on disk, the only layer that is persisted
3. session overrides      — set when a session starts, discarded when it stops
4. runtime changes        — what the settings UI writes while the app is running

Only sparse overlays are stored above layer 1, so a default that changes in a later release reaches
users who never touched that setting.
"""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .defaults import default_layer
from .hotswap import HotSwapClass, classify, classify_many
from .presets import preset_overlay
from .schema import AppConfig

logger = logging.getLogger(__name__)

LAYER_USER = "user"
LAYER_SESSION = "session"
LAYER_RUNTIME = "runtime"
WRITABLE_LAYERS = (LAYER_USER, LAYER_SESSION, LAYER_RUNTIME)

DEFAULT_CONFIG_FILENAME = "transcriber-config.json"


def default_config_path() -> Path:
    """Resolve the user config file location, honouring ``TRANSCRIBER_CONFIG_PATH``."""
    override = os.environ.get("TRANSCRIBER_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    return Path("./data") / DEFAULT_CONFIG_FILENAME


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto a copy of ``base``.

    Lists replace wholesale rather than merging element-wise — a partially merged list of quick
    actions would be meaningless.
    """
    result = deepcopy(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = deepcopy(value)
    return result


def set_in(target: dict[str, Any], path: str, value: Any) -> None:
    """Write ``value`` at a dotted ``path``, creating intermediate dicts as needed."""
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def get_in(source: dict[str, Any], path: str) -> Any:
    """Read the value at a dotted ``path``, or ``None`` if any segment is missing."""
    cursor: Any = source
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


class ConfigStore:
    """Owns the configuration layers and produces a validated :class:`AppConfig`.

    The backend is the source of truth for configuration (FE §8.1). The frontend reads from here,
    writes changes back here, and re-reads — it never keeps a parallel notion of what the settings
    are.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or default_config_path()
        self._layers: dict[str, dict[str, Any]] = {
            LAYER_USER: {},
            LAYER_SESSION: {},
            LAYER_RUNTIME: {},
        }
        self._cached: AppConfig | None = None

    # -- resolution ----------------------------------------------------------------

    @property
    def path(self) -> Path:
        """The user config file location."""
        return self._path

    def resolve(self) -> AppConfig:
        """Return the validated configuration produced by merging every layer."""
        if self._cached is None:
            merged = default_layer()
            for name in WRITABLE_LAYERS:
                merged = deep_merge(merged, self._layers[name])
            self._cached = AppConfig.model_validate(merged)
        return self._cached

    def as_dict(self) -> dict[str, Any]:
        """Return the resolved configuration as a JSON-safe dict, for the HTTP surface."""
        return self.resolve().model_dump(mode="json")

    def layer(self, name: str) -> dict[str, Any]:
        """Return a copy of one writable layer's sparse overlay."""
        self._require_writable(name)
        return deepcopy(self._layers[name])

    # -- mutation ------------------------------------------------------------------

    def set(self, path: str, value: Any, layer: str = LAYER_RUNTIME) -> HotSwapClass:
        """Set one dotted ``path`` in ``layer`` and return what applying it costs.

        The write is validated before it is kept: an invalid value leaves the store untouched and
        raises, rather than poisoning the resolved configuration.
        """
        return self.update({path: value}, layer=layer)

    def update(self, changes: dict[str, Any], layer: str = LAYER_RUNTIME) -> HotSwapClass:
        """Apply several dotted-path changes atomically and return the worst hot-swap class."""
        self._require_writable(layer)
        if not changes:
            return HotSwapClass.LIVE

        candidate = deepcopy(self._layers[layer])
        for path, value in changes.items():
            set_in(candidate, path, value)

        self._validate_with(layer, candidate)
        self._layers[layer] = candidate
        self._cached = None
        return classify_many(list(changes))

    def apply_preset(self, name: str, layer: str = LAYER_RUNTIME) -> HotSwapClass:
        """Overlay a named preset (BE §13.4) onto ``layer``."""
        self._require_writable(layer)
        overlay = preset_overlay(name)
        candidate = deep_merge(self._layers[layer], overlay)
        self._validate_with(layer, candidate)
        self._layers[layer] = candidate
        self._cached = None
        return classify_many(_dotted_paths(overlay))

    def clear_layer(self, name: str) -> None:
        """Drop a writable layer entirely — used when a session ends."""
        self._require_writable(name)
        self._layers[name] = {}
        self._cached = None

    def classify(self, path: str) -> HotSwapClass:
        """Expose the hot-swap classification for a single dotted path."""
        return classify(path)

    # -- persistence ---------------------------------------------------------------

    def load(self) -> None:
        """Load the user layer from disk. A missing or unreadable file is not fatal."""
        if not self._path.exists():
            logger.debug("No config file at %s; using defaults", self._path)
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable config at %s: %s", self._path, exc)
            return
        if not isinstance(raw, dict):
            logger.warning("Ignoring config at %s: expected a JSON object", self._path)
            return
        try:
            self._validate_with(LAYER_USER, raw)
        except ValueError as exc:
            logger.warning("Ignoring invalid config at %s: %s", self._path, exc)
            return
        self._layers[LAYER_USER] = raw
        self._cached = None

    def save(self) -> None:
        """Persist the user layer to disk.

        Runtime changes are folded down into the user layer first, so what the user configured in
        the UI survives a restart. Nothing secret is written — credentials never enter any layer.
        """
        merged = deep_merge(self._layers[LAYER_USER], self._layers[LAYER_RUNTIME])
        self._layers[LAYER_USER] = merged
        self._layers[LAYER_RUNTIME] = {}
        self._cached = None

        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f"{self._path.suffix}.tmp")
        tmp.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    # -- internals -----------------------------------------------------------------

    def _require_writable(self, name: str) -> None:
        if name not in WRITABLE_LAYERS:
            raise KeyError(f"Unknown layer {name!r}. Writable layers: {', '.join(WRITABLE_LAYERS)}")

    def _validate_with(self, layer: str, candidate: dict[str, Any]) -> None:
        """Validate the full merge with ``candidate`` substituted for ``layer``."""
        merged = default_layer()
        for name in WRITABLE_LAYERS:
            overlay = candidate if name == layer else self._layers[name]
            merged = deep_merge(merged, overlay)
        AppConfig.model_validate(merged)


def _dotted_paths(overlay: dict[str, Any], prefix: str = "") -> list[str]:
    """Flatten a sparse overlay into the dotted paths it touches."""
    paths: list[str] = []
    for key, value in overlay.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths.extend(_dotted_paths(value, prefix=f"{path}."))
        else:
            paths.append(path)
    return paths
