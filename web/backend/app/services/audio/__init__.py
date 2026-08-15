"""Audio capture (BE §4).

The canonical format — 16 kHz, mono, float32, −1.0…+1.0 — is enforced at this boundary and nowhere
else. Everything downstream may assume it without checking.

Layout:

* ``formats``     — the canonical format and conversion into it
* ``resample``    — band-limited sample-rate conversion
* ``ring_buffer`` — the fixed-capacity buffer that keeps capture from ever blocking
* ``level``       — RMS, peak, and clipping for the input meter
* ``preprocess``  — optional high-pass filtering and gain normalisation
* ``devices``     — merged enumeration of microphones and loopback devices
* ``sources``     — device, WAV file, and synthetic frame producers
"""

from .devices import (
    FILE_DEVICE,
    AudioDevice,
    DeviceEnumerationError,
    device_support_available,
    find_device,
    list_devices,
)
from .formats import (
    CHANNELS,
    DTYPE,
    SAMPLE_RATE,
    AudioFormatError,
    clip_to_range,
    downmix,
    duration_seconds,
    frame_samples,
    is_canonical,
    to_canonical,
    to_float32,
)
from .level import AudioLevel, LevelMeter, measure, to_dbfs
from .library import AudioFile, AudioLibraryError, list_files, store_upload
from .preprocess import GainNormaliser, HighPassFilter, PreprocessChain
from .resample import resample, resampler_name
from .ring_buffer import AudioRingBuffer, RingBufferStats
from .sources import AudioSource, DeviceSource, SourceInfo, SyntheticSource, WavFileSource

__all__ = [
    "CHANNELS",
    "DTYPE",
    "FILE_DEVICE",
    "SAMPLE_RATE",
    "AudioDevice",
    "AudioFile",
    "AudioFormatError",
    "AudioLevel",
    "AudioLibraryError",
    "AudioRingBuffer",
    "AudioSource",
    "DeviceEnumerationError",
    "DeviceSource",
    "GainNormaliser",
    "HighPassFilter",
    "LevelMeter",
    "PreprocessChain",
    "RingBufferStats",
    "SourceInfo",
    "SyntheticSource",
    "WavFileSource",
    "clip_to_range",
    "device_support_available",
    "downmix",
    "duration_seconds",
    "find_device",
    "frame_samples",
    "is_canonical",
    "list_devices",
    "list_files",
    "measure",
    "resample",
    "resampler_name",
    "store_upload",
    "to_canonical",
    "to_dbfs",
    "to_float32",
]
