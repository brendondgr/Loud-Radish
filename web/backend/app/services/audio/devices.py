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
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

DeviceKind = Literal["microphone", "loopback", "file"]

#: The synthetic device representing "read a WAV file instead of a device".
FILE_DEVICE_ID = "file"


@dataclass(frozen=True)
class AudioDevice:
    """One selectable capture source, as presented to the frontend.

    ``id`` is derived from the device's **name**, not from its PortAudio index, and that is the
    single most important thing about this class.

    PortAudio indices are positional and shift whenever the host re-enumerates — plugging in a USB
    microphone, unplugging one, a dock waking up. Observed directly during development: a Samson
    GoMic sat at index 6, was unplugged, and index 6 became ``sysdefault``. A configuration holding
    "device 6" would then have opened a completely different input, successfully and silently, and
    recorded the whole talk from the wrong microphone with nothing to indicate it.

    So the stored identity is stable and the index is resolved fresh at open time.
    """

    id: str
    name: str
    kind: DeviceKind
    channels: int = 1
    default_sample_rate: int = 16_000
    is_default: bool = False
    backend: str = "sounddevice"
    note: str = field(default="")
    #: Current PortAudio index. Valid only for as long as this enumeration is; never persisted.
    index: int | None = None

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
            "index": self.index,
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

        name = str(info.get("name", f"Device {index}"))
        # Plumbing is hidden unless the host has made it the default, in which case hiding it would
        # remove the entry the picker is currently pointing at.
        if _is_plumbing(name) and index != default_input:
            continue

        presented = _present(name, index == default_input)
        devices.append(
            AudioDevice(
                id=stable_id(presented),
                name=presented,
                index=index,
                kind=_classify(info),
                channels=int(info.get("max_input_channels", 1)),
                default_sample_rate=int(info.get("default_samplerate", 16_000) or 16_000),
                is_default=index == default_input,
            )
        )

    return devices


_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def stable_id(name: str) -> str:
    """A durable identifier for a device, derived from its name.

    Names are what a person recognises and what the host reports consistently across
    re-enumeration; indices are neither. Two devices reporting an identical name are genuinely
    indistinguishable to us, and picking either is as correct as we can be.
    """
    slug = _SLUG_UNSAFE.sub("-", name.strip().lower()).strip("-")
    return slug or "device"


def _present(name: str, is_default: bool) -> str:
    """Tidy a device name for a picker.

    ``default`` on its own tells a user nothing about what they are about to record, so the one
    entry that is only ever a name is given a description instead.
    """
    if is_default and name.strip().lower() in _ALSA_PLUMBING | {"default"}:
        return "System default input"
    return name


def _default_input_index(sd: Any) -> int | None:
    """The host's default input device index, or ``None`` if it has none.

    ``sounddevice.default.device`` is an ``_InputOutputPair`` — subscriptable, but **not** a list
    or a tuple. An earlier version type-checked for those, fell through to treating the pair itself
    as an integer, and so returned ``None`` on every real machine. Nothing was ever marked as the
    default, and :func:`find_device` with no id silently picked the first microphone in enumeration
    order instead of the one chosen in the system's own sound settings.

    Subscripting is therefore attempted first and the type checked afterwards, which handles the
    pair, a plain sequence, and a bare integer alike.
    """
    try:
        default = sd.default.device
    except Exception:  # noqa: BLE001 - not every host API exposes a default
        return None

    try:
        value = default[0]
    except (TypeError, IndexError, KeyError):
        value = default

    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if index >= 0 else None


#: Names that are ALSA plumbing rather than anything a person chose to plug in.
#:
#: On a PipeWire desktop PortAudio reports these alongside the real hardware, and they are worse
#: than noise in a picker: ``sysdefault`` and ``pipewire`` are aliases for whatever ``default``
#: already points at, and ``spdif`` is an output. Presenting fourteen entries of which three are
#: real microphones makes the one useful control in this dialog hard to use.
#:
#: ``default`` is deliberately **not** here. It is the system default, it is what most users want,
#: and it is the only entry that keeps working when hardware is unplugged.
_ALSA_PLUMBING = frozenset({"sysdefault", "pipewire", "spdif", "jack", "pulse", "oss", "speex"})

#: Substrings that mark a capture source as playback being fed back rather than a microphone.
#:
#: Host APIs expose no loopback flag, so this is naming conventions: WASAPI appends "(loopback)",
#: PulseAudio and PipeWire use ".monitor" or "Monitor of …", and older Windows drivers say
#: "Stereo Mix". The HDMI and application-name cases come from PipeWire's JACK bridge, which
#: presents every *output* — a graphics card's HDMI sink, a running media player — as a capture
#: source indistinguishable from a microphone by anything except its name.
_LOOPBACK_MARKERS = (
    "loopback",
    "monitor of",
    ".monitor",
    "stereo mix",
    "what u hear",
    "wave out mix",
    "hdmi",
    "digital stereo (hdmi",
    "digital output",
)


def _classify(info: dict[str, Any]) -> DeviceKind:
    """Decide whether a capture source is a microphone or system playback fed back."""
    name = str(info.get("name", "")).lower()
    if any(marker in name for marker in _LOOPBACK_MARKERS):
        return "loopback"

    # PipeWire's JACK bridge names an application's own output stream "App/binary" — Spotify's
    # playback appears as "Spotify/spotify". A slash never occurs in a hardware device's name, and
    # capturing one of these is capturing what that application is playing.
    if "/" in name and not name.startswith("hw:"):
        return "loopback"

    return "microphone"


def _is_plumbing(name: str) -> bool:
    """Whether a device name is an ALSA alias rather than a real selectable input."""
    return name.strip().lower() in _ALSA_PLUMBING


def default_device(devices: list[AudioDevice] | None = None) -> AudioDevice | None:
    """The device to use when nothing has been chosen: the host's default, then any microphone."""
    available = devices if devices is not None else list_devices()
    return next(
        (device for device in available if device.is_default),
        next((device for device in available if device.kind == "microphone"), None),
    )


def find_device(device_id: str | None) -> AudioDevice | None:
    """Resolve a stored device identifier against the devices present *now*.

    Three forms are accepted, in order of preference:

    1. a :func:`stable_id` slug — what this application stores;
    2. an exact device name — what a user might reasonably paste in;
    3. a bare PortAudio index — what earlier versions stored.

    The third is honoured for compatibility and is the reason this function exists in this shape.
    An index is positional: unplug a USB microphone and every index above it shifts down, so a
    stored ``"6"`` can silently resolve to an entirely different input. It is therefore matched
    **last**, and only when nothing better matches.
    """
    devices = list_devices()
    if device_id is None:
        return default_device(devices)

    wanted = device_id.strip()
    for match in (
        lambda d: d.id == wanted,
        lambda d: d.name == wanted,
        lambda d: stable_id(d.name) == stable_id(wanted),
    ):
        found = next((device for device in devices if match(device)), None)
        if found is not None:
            return found

    if wanted.isdigit():
        logger.info(
            "Resolving audio device %r by PortAudio index. Indices shift when hardware changes; "
            "re-select the device in settings to store a stable identifier instead.",
            wanted,
        )
        return next((device for device in devices if device.index == int(wanted)), None)

    return None
