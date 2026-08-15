"""Backend registration and construction.

The registry is what turns ``asr.backend = "faster-whisper"`` in a config file into an object. It
also answers "what can this machine actually run?" without instantiating anything expensive, which
is what the model-selection UI needs: every option listed with the information required to choose
between them, including whether it is installed at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ...config.schema import AsrConfig
from .contract import AsrBackend, AsrCapabilities, AsrUnavailableError

#: Builds a backend from configuration. Registered per backend id.
BackendFactory = Callable[[AsrConfig], AsrBackend]


@dataclass(frozen=True)
class BackendInfo:
    """What the model-selection UI needs to present one option (FE §7.2)."""

    backend_id: str
    name: str
    family: str
    available: bool
    #: Why it is unavailable, and what to do about it. Empty when available.
    unavailable_reason: str
    capabilities: AsrCapabilities
    #: Model names this backend offers, with a rough size for each.
    models: list[dict[str, Any]]
    #: Devices and precisions *this machine* can run, keyed by device. Empty for backends where the
    #: question does not arise. Offering a precision the host cannot run is offering a setting that
    #: fails at load time, long after it was chosen.
    compute: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``GET /api/asr/models``."""
        return {
            "backend": self.backend_id,
            "name": self.name,
            "family": self.family,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "capabilities": self.capabilities.as_dict(),
            "models": list(self.models),
            "compute": {key: list(value) for key, value in self.compute.items()},
        }


_FACTORIES: dict[str, BackendFactory] = {}
_INFO: dict[str, Callable[[], BackendInfo]] = {}


def register(backend_id: str, factory: BackendFactory, info: Callable[[], BackendInfo]) -> None:
    """Register a backend and the description of it shown in the UI."""
    _FACTORIES[backend_id] = factory
    _INFO[backend_id] = info


def registered_ids() -> list[str]:
    """Every registered backend id, in registration order."""
    return list(_FACTORIES)


def available_backends() -> list[BackendInfo]:
    """Describe every backend, including the ones that cannot run here.

    Unavailable backends are listed rather than hidden, with the reason and the remedy attached: a
    model that silently does not appear looks like a missing feature, whereas one greyed out with
    "install the asr-whisper extra" is actionable.
    """
    return [describe() for describe in _INFO.values()]


def build_backend(config: AsrConfig) -> AsrBackend:
    """Construct the configured backend.

    Raises:
        AsrUnavailableError: when the backend id is unknown, listing what is registered.
    """
    factory = _FACTORIES.get(config.backend)
    if factory is None:
        known = ", ".join(registered_ids()) or "none"
        raise AsrUnavailableError(
            f"Unknown ASR backend {config.backend!r}. Registered backends: {known}."
        )
    return factory(config)


# -- built-in registrations --------------------------------------------------------


def _build_mock(config: AsrConfig) -> AsrBackend:
    from .mock import MockAsrBackend, default_script

    return MockAsrBackend(default_script(), model_id=f"mock:{config.model}")


def _mock_info() -> BackendInfo:
    from .mock import MockAsrBackend

    return BackendInfo(
        backend_id="mock",
        name="Scripted mock",
        family="test",
        available=True,
        unavailable_reason="",
        capabilities=MockAsrBackend().capabilities,
        models=[{"name": "scripted", "size": "0 MB", "note": "Replays predetermined output"}],
    )


def _build_faster_whisper(config: AsrConfig) -> AsrBackend:
    from .faster_whisper import FasterWhisperBackend

    return FasterWhisperBackend(
        model=config.model,
        device=config.device,
        precision=config.precision,
        language=config.language,
        beam_size=config.beam_size,
    )


def _faster_whisper_info() -> BackendInfo:
    from .faster_whisper import FasterWhisperBackend, compute_support, is_available

    available = is_available()
    return BackendInfo(
        backend_id="faster-whisper",
        name="Whisper (faster-whisper)",
        family="Autoregressive encoder-decoder",
        available=available,
        unavailable_reason=(
            "" if available else "Not installed. Add it with: uv sync --extra asr-whisper"
        ),
        capabilities=FasterWhisperBackend().capabilities,
        models=[
            {"name": "tiny", "size": "75 MB", "note": "Fastest; noticeably less accurate"},
            {"name": "base", "size": "142 MB", "note": "Usable on modest CPUs"},
            {"name": "small", "size": "466 MB", "note": "The recommended starting point on a CPU"},
            {
                "name": "large-v3-turbo",
                "size": "1.6 GB",
                # A distilled large-v3 with four decoder layers instead of thirty-two. Nearly all
                # of large-v3's accuracy at roughly four times its speed, which on a GPU makes it
                # the only large-class model that keeps up with a live talk. Measured on a Radeon
                # 8060S: 40x batch real-time, the same as `small`, from a large-v3-derived model.
                "note": "Best accuracy that still keeps up live. The right default with a GPU.",
            },
            {
                "name": "medium",
                "size": "1.5 GB",
                "note": "Superseded by large-v3-turbo, which is faster and more accurate",
            },
            {
                "name": "large-v3",
                "size": "3.1 GB",
                "note": "Most accurate; too slow to keep up live on most hardware",
            },
        ],
        compute=compute_support() if available else {},
    )


register("mock", _build_mock, _mock_info)
register("faster-whisper", _build_faster_whisper, _faster_whisper_info)
