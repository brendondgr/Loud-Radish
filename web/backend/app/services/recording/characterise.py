"""Measuring a recording's audio, so an empty transcript can explain itself.

``state: done, transcribed_seconds: 322.46, 2 segments`` is unfalsifiable from outside. It is
exactly what a broken transcriber looks like, and exactly what an accurate one looks like when the
recording contained music. Five minutes of investigation settled which — by measuring RMS, the band
energy distribution and the dominant frequency, and finding 17.9 % of the energy below 100 Hz with a
peak at 52 Hz, which is not a human voice.

**That investigation is what this module makes permanent.** Every recording carries its own
measurements, so the same question is answered by reading a file rather than by an afternoon with
`ffmpeg` and `numpy`. Research §9.2 names the second benefit: once every recording is measured, a
regression is a diff between two JSON documents rather than an argument.

Nothing here interprets audio for the user. It reports numbers and one cautious label, and the label
says *likely*.
"""

from __future__ import annotations

import logging
import math
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

import numpy as np

logger = logging.getLogger(__name__)

#: The bands the brief's investigation used, kept identical so old measurements stay comparable.
BANDS: Final[tuple[tuple[int, int], ...]] = (
    (20, 100),
    (100, 300),
    (300, 1000),
    (1000, 3000),
    (3000, 6000),
    (6000, 8000),
)

#: Where a human voice lives. A fundamental sits at roughly 85–255 Hz and the formants that carry
#: intelligibility run to about 3.5 kHz; speech carries very little below 100 Hz, which is what
#: separates it from music on this measure.
SPEECH_BAND: Final = (300, 3000)

#: Below this share of energy in the speech band, calling the content speech is not supportable.
SPEECH_BAND_FLOOR: Final = 0.35

#: Below this, the recording is silent for practical purposes rather than merely quiet.
SILENCE_DBFS: Final = -50.0


@dataclass
class AudioCharacter:
    """What the audio in a recording actually is, measured rather than assumed."""

    duration_s: float = 0.0
    sample_rate: int = 0
    rms: float = 0.0
    peak: float = 0.0
    #: True when samples exceeded full scale. Monitor taps are pre-volume and can exceed unity,
    #: and clipped audio both distorts on encode and hurts transcription.
    clipped: bool = False
    audible_pct: float = 0.0
    dominant_hz: float = 0.0
    band_energy_pct: dict[str, float] = field(default_factory=dict)
    speech_band_ratio: float = 0.0
    likely_content: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def characterise(path: Path | str) -> AudioCharacter:
    """Measure a WAV file. Never raises — a failed measurement must not fail a recording."""
    try:
        samples, rate = _read(Path(path))
    except (OSError, wave.Error, ValueError) as exc:
        logger.warning("Could not characterise %s: %s", path, exc)
        return AudioCharacter()

    if samples.size == 0 or rate <= 0:
        return AudioCharacter(sample_rate=max(rate, 0))

    finite = samples[np.isfinite(samples)]
    if finite.size == 0:
        # Every sample non-finite is the AU-header fault. Reported rather than crashed, because
        # this runs after the recording exists and the audio file itself is still on disk.
        logger.warning("%s contains no finite samples at all.", path)
        return AudioCharacter(duration_s=samples.size / rate, sample_rate=rate)

    return AudioCharacter(
        duration_s=round(samples.size / rate, 2),
        sample_rate=rate,
        rms=round(float(np.sqrt(np.mean(finite**2))), 6),
        peak=round(float(np.max(np.abs(finite))), 6),
        clipped=bool(np.max(np.abs(finite)) > 1.0),
        audible_pct=_audible_pct(finite, rate),
        **_spectrum(finite, rate),
    )


def _read(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV as mono float32, whatever width and channel count it was written at."""
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        raw = handle.readframes(handle.getnframes())

    dtype = {1: np.int8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise ValueError(f"unsupported sample width: {width} bytes")

    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    data /= float(np.iinfo(dtype).max)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def _audible_pct(samples: np.ndarray, rate: int) -> float:
    """What share of one-second windows carry anything above the silence floor.

    A recording that is 100 % audible and still transcribes to nothing is a very different report
    from one that was silent — the first says the capture worked and the content was not speech.
    """
    per_second = samples[: samples.size // rate * rate]
    if per_second.size == 0:
        return 0.0
    windows = per_second.reshape(-1, rate)
    rms = np.sqrt(np.mean(windows**2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    return round(float((db > SILENCE_DBFS).mean() * 100), 1)


def _spectrum(samples: np.ndarray, rate: int) -> dict[str, Any]:
    """Band energy, dominant frequency, and what the shape suggests the content is."""
    # A bounded window: the shape of a talk does not change over five minutes, and transforming
    # the whole file would cost seconds for no extra certainty.
    window = samples[: min(samples.size, rate * 20)]
    if window.size < rate:
        return {
            "dominant_hz": 0.0,
            "band_energy_pct": {},
            "speech_band_ratio": 0.0,
            "likely_content": "too_short",
        }

    spectrum = np.abs(np.fft.rfft(window * np.hanning(window.size)))
    freqs = np.fft.rfftfreq(window.size, 1 / rate)

    def energy(low: float, high: float) -> float:
        return float(spectrum[(freqs >= low) & (freqs < high)].sum())

    total = energy(20, 8000)
    if total <= 0:
        return {
            "dominant_hz": 0.0,
            "band_energy_pct": {},
            "speech_band_ratio": 0.0,
            "likely_content": "silent",
        }

    bands = {f"{low}-{high}": round(100 * energy(low, high) / total, 1) for low, high in BANDS}
    ratio = energy(*SPEECH_BAND) / total
    dominant = float(freqs[int(np.argmax(spectrum))])

    return {
        "dominant_hz": round(dominant, 1),
        "band_energy_pct": bands,
        "speech_band_ratio": round(ratio, 3),
        "likely_content": _label(ratio, dominant),
    }


def _label(speech_ratio: float, dominant_hz: float) -> str:
    """A cautious guess at what the audio is. Hedged on purpose — it informs, it does not decide.

    Nothing downstream branches on this. It exists so that a person reading the sidecar sees a
    plain-language summary beside the numbers rather than having to interpret a band table.
    """
    if math.isnan(speech_ratio):
        return "unknown"
    if speech_ratio >= SPEECH_BAND_FLOOR and 60 <= dominant_hz <= 4000:
        return "likely_speech"
    if dominant_hz < 100:
        return "likely_music_or_game"
    return "unclear"
