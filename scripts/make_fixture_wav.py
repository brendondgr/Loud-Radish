#!/usr/bin/env python
"""Generate small synthetic WAV files for testing the audio pipeline.

The streaming engine is developed against recorded audio because a live microphone is not
reproducible. Real recordings are large and often not shareable, so this produces deterministic
stand-ins with a known speech/silence structure — enough to exercise the VAD, the commit
timeout, and the silence gate without shipping audio fixtures.

Usage::

    uv run python scripts/make_fixture_wav.py --out data/fixtures
    uv run python scripts/make_fixture_wav.py --pattern continuous --seconds 120 --out /tmp
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "web" / "backend"))

from app.services.audio.formats import SAMPLE_RATE  # noqa: E402

#: Named speech/silence structures, each exercising a specific pipeline behaviour.
#: Syllable-rate amplitude modulation, so generated audio reads as speech rather than as a tone.
SYLLABLE_HZ = 4.0
SYLLABLE_FLOOR = 0.12

PATTERNS: dict[str, str] = {
    "alternating": "Speech and silence in turn — the ordinary case.",
    "continuous": "Unbroken speech with no qualifying pause — exercises the commit timeout.",
    "silent": "Silence throughout — exercises the hallucination gate.",
    "sparse": "Long silences with brief speech — exercises VAD hysteresis.",
}


def build(pattern: str, seconds: float, sample_rate: int) -> np.ndarray:
    """Render a named pattern to normalised float samples."""
    total = int(round(seconds * sample_rate))
    t = np.arange(total, dtype=np.float64) / sample_rate

    if pattern == "silent":
        return np.zeros(total, dtype=np.float32)

    # A three-tone mixture in the speech band. Not speech, but structured enough that a level-based
    # detector behaves as it would on real input.
    tone = (
        0.45 * np.sin(2 * np.pi * 180 * t)
        + 0.28 * np.sin(2 * np.pi * 420 * t)
        + 0.14 * np.sin(2 * np.pi * 950 * t)
    )

    # Real speech varies at syllable rate; a constant-level tone is a fan, not a talker, and any
    # detector with an adaptive noise floor will rightly learn it as background. Every pattern
    # therefore carries a syllable envelope, including the "continuous" one.
    syllables = SYLLABLE_FLOOR + (1.0 - SYLLABLE_FLOOR) * (
        0.5 + 0.5 * np.sin(2 * np.pi * SYLLABLE_HZ * t)
    )

    if pattern == "continuous":
        envelope = syllables
    elif pattern == "sparse":
        envelope = _gate(t, on=1.5, off=8.0) * syllables
    else:
        envelope = _gate(t, on=4.0, off=1.2) * syllables

    signal = tone * envelope * 0.5
    peak = float(np.max(np.abs(signal))) or 1.0
    return (signal / peak * 0.7).astype(np.float32)


def _gate(t: np.ndarray, on: float, off: float) -> np.ndarray:
    """A square on/off envelope with short ramps, so gating does not produce clicks."""
    period = on + off
    phase = np.mod(t, period)
    gate = (phase < on).astype(np.float64)
    ramp = 0.02
    rising = np.clip(phase / ramp, 0.0, 1.0)
    falling = np.clip((on - phase) / ramp, 0.0, 1.0)
    return gate * np.minimum(rising, falling)


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """Write float samples as 16-bit PCM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "fixtures")
    parser.add_argument("--pattern", choices=[*PATTERNS, "all"], default="all")
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--rate", type=int, default=SAMPLE_RATE)
    args = parser.parse_args(argv)

    patterns = list(PATTERNS) if args.pattern == "all" else [args.pattern]
    for name in patterns:
        path = args.out / f"{name}-{int(args.seconds)}s.wav"
        write_wav(path, build(name, args.seconds, args.rate), args.rate)
        print(f"{path}  ({args.seconds:.0f}s @ {args.rate} Hz)  — {PATTERNS[name]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
