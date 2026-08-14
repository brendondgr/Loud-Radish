"""Capture device enumeration (BE §4.3).

Two capture sources, both selectable at runtime:

**Microphone.** The default. A laptop's built-in microphone in a lecture hall captures a distant,
reverberant speaker plus ambient noise, so expect word error rates well above published benchmarks.
The single highest-leverage improvement to overall quality is a directional or clip-on USB
microphone — larger than any model upgrade.

**System loopback.** Captures what the machine is playing, which is essential for remote talks and
gives dramatically cleaner audio. The architectural point that matters here: **loopback devices are
enumerated separately from input devices**, so this module merges both lists and tags every entry
with its type. A device list that silently omits loopback devices looks like the feature is missing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

DeviceKind = Literal["microphone", "loopback", "file"]

#: The synthetic device representing "read a WAV file instead of a device".
FILE_DEVICE_ID = "file"


@dataclass(frozen=True)
class AudioDevice:
    """One selectable capture source, as presented to the frontend."""

    id: str
    name: str
    kind: DeviceKind
    channels: int = 1
    default_sample_rate: int = 16_000
    is_default: bool = False
    backend: str = "sounddevice"
    note: str = field(default="")

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``GET /api/audio/devices``."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "channels": self.channels,
            "default_sample_rate": self.default_sample_rate,
            "is_default": self.is_default,
            "backend": self.backend,
            "note": self.note,
        }


FILE_DEVICE = AudioDevice(
    id=FILE_DEVICE_ID,
    name="Audio file",
    kind="file",
    backend="file",
    note="Reads a WAV file at real-time speed. Use this to develop and test against a recording.",
)


class DeviceEnumerationError(RuntimeError):
    """Raised when devices cannot be listed at all."""


def _sounddevice() -> Any | None:
    """Return the ``sounddevice`` module, or ``None`` if the optional group is absent."""
    try:
        import sounddevice
    except (ImportError, OSError) as exc:
        # OSError covers a present package with no PortAudio library behind it.
        logger.debug("sounddevice unavailable: %s", exc)
        return None
    return sounddevice


def device_support_available() -> bool:
    """Whether live device capture is possible in this environment."""
    return _sounddevice() is not None


def list_devices(include_file: bool = True) -> list[AudioDevice]:
    """Return every selectable capture source, microphones and loopbacks in one list.

    The file source is included by default so it is always selectable — it is the reproducible input
    the whole pipeline is developed against, and hiding it behind a separate control makes testing
    awkward for no benefit.
    """
    devices: list[AudioDevice] = []
    if include_file:
        devices.append(FILE_DEVICE)

    sd = _sounddevice()
    if sd is None:
        logger.info(
            "No audio device backend; only the file source is available. "
            "Install with: uv sync --extra audio-device"
        )
        return devices

    try:
        raw = sd.query_devices()
        default_input = _default_input_index(sd)
    except Exception as exc:  # noqa: BLE001 - PortAudio raises a variety of host errors
        raise DeviceEnumerationError(
            f"Could not list audio devices ({type(exc).__name__}). "
            "Check that the system audio service is running."
        ) from exc

    for index, info in enumerate(raw):
        if int(info.get("max_input_channels", 0)) <= 0:
            continue
        devices.append(
            AudioDevice(
                id=str(index),
                name=str(info.get("name", f"Device {index}")),
                kind=_classify(info),
                channels=int(info.get("max_input_channels", 1)),
                default_sample_rate=int(info.get("default_samplerate", 16_000) or 16_000),
                is_default=index == default_input,
            )
        )

    return devices


def _default_input_index(sd: Any) -> int | None:
    """The host's default input device index, or ``None`` if it has none."""
    try:
        default = sd.default.device
    except Exception:  # noqa: BLE001 - not every host API exposes a default
        return None
    if isinstance(default, (list, tuple)) and default:
        value = default[0]
    else:
        value = default
    return int(value) if isinstance(value, int) and value >= 0 else None


def _classify(info: dict[str, Any]) -> DeviceKind:
    """Decide whether a device is a microphone or a loopback capture.

    Host APIs do not expose a loopback flag, so this matches the naming conventions each platform
    uses: WASAPI appends "(loopback)", PulseAudio and PipeWire use ".monitor", and JACK-style setups
    often say "Monitor of ...".
    """
    name = str(info.get("name", "")).lower()
    markers = ("loopback", "monitor of", ".monitor", "stereo mix", "what u hear", "wave out mix")
    return "loopback" if any(marker in name for marker in markers) else "microphone"


def find_device(device_id: str | None) -> AudioDevice | None:
    """Look a device up by id, or return the default when ``device_id`` is ``None``."""
    devices = list_devices()
    if device_id is not None:
        return next((device for device in devices if device.id == device_id), None)
    return next(
        (device for device in devices if device.is_default),
        next((device for device in devices if device.kind == "microphone"), None),
    )
