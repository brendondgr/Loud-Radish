#!/usr/bin/env python
"""Measure whether this machine can encode video and transcribe speech at the same time (D-022).

The open question Plan 4 was written around. There is no hardware encoder on this class of machine,
so video is software VP8 on the same CPU that runs the speech model — and whether both fit is a
measurement, not a guess.

    uv run scripts/measure_capture_cost.py

It runs three passes over the same fixture: transcription alone, video encoding alone, and both at
once. What matters is the **real-time factor of the speech model in the third pass**. Below 1.0 the
transcript falls behind the speaker and never catches up, which is the failure the whole question is
about; the documented remedy is to turn live transcription off and let the post-capture pass do it.

Uses `videotestsrc` rather than a real window: the encoder does not care where its frames came from,
and needing a human to pick a window would make this unrunnable in the one place it matters, which
is on somebody else's hardware.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "web" / "backend"))

import numpy as np  # noqa: E402
from app.config import ConfigStore  # noqa: E402
from app.services.audio.formats import SAMPLE_RATE  # noqa: E402
from app.services.capture import detect  # noqa: E402
from app.services.capture.pipeline import encoder_args  # noqa: E402


def write_fixture(path: Path, seconds: float) -> Path:
    """Syllable-modulated tone — enough structure that the model does real work on it."""
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    tone = (
        0.45 * np.sin(2 * np.pi * 180 * t)
        + 0.28 * np.sin(2 * np.pi * 420 * t)
        + 0.14 * np.sin(2 * np.pi * 950 * t)
    )
    syllables = 0.12 + 0.88 * (0.5 + 0.5 * np.sin(2 * np.pi * 4.0 * t))
    signal = np.clip(tone * syllables * 0.35, -1.0, 1.0)

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((signal * 32767).astype(np.int16).tobytes())
    return path


def video_command(support, out: Path, seconds: float, fps: int, height: int) -> list[str]:
    """The same pipeline shape the recorder builds, fed by a test pattern instead of a window."""
    return [
        "gst-launch-1.0",
        "-q",
        "-e",
        "videotestsrc",
        f"num-buffers={int(seconds * fps)}",
        "!",
        f"video/x-raw,framerate={fps}/1,width={int(height * 16 / 9)},height={height}",
        "!",
        "videoconvert",
        "!",
        *encoder_args(support.encoder),
        "!",
        support.muxer,
        "!",
        "filesink",
        f"location={out}",
    ]


def transcribe(audio: Path, backend: str, model: str) -> tuple[float, int]:
    """Transcribe the fixture whole. Returns `(wall seconds, segments)`."""
    import asyncio

    from app.services.asr.lifecycle import AsrLifecycle
    from app.services.recording import transcribe_file

    store = ConfigStore()
    # `vad_filter` off, deliberately. It is on by default and it works (D-019): given a synthetic
    # tone it strips almost everything before the decoder sees it, so the model finishes in a
    # fraction of a second and the real-time factor comes out at 150× — a number that measures the
    # filter rather than the encoder contention this script exists to measure. Turning it off
    # forces a full decode of every window, which is the work a real talk would cost.
    config = store.resolve().asr.model_copy(
        update={"backend": backend, "model": model, "vad_filter": False}
    )
    lifecycle = AsrLifecycle(config)
    asyncio.run(lifecycle.load(config))

    started = time.monotonic()
    segments = transcribe_file(audio, transcribe=lifecycle.transcribe, window_s=30.0)
    elapsed = time.monotonic() - started

    asyncio.run(lifecycle.unload())
    return elapsed, len(segments)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=60.0, help="Fixture length.")
    parser.add_argument(
        "--audio",
        type=Path,
        default=None,
        help=(
            "A WAV of REAL SPEECH, 16 kHz mono. Strongly recommended — see the note this script "
            "prints when run without one."
        ),
    )
    parser.add_argument("--backend", default="faster-whisper")
    parser.add_argument("--model", default="small")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--height", type=int, default=720)
    args = parser.parse_args()

    support = detect()
    if not support.available:
        print(f"Window capture is unavailable here: {support.reason}", file=sys.stderr)
        return 2

    work = REPO_ROOT / "data" / "measure"
    if args.audio is not None:
        audio = args.audio
        if not audio.is_file():
            print(f"No such file: {audio}", file=sys.stderr)
            return 2
        with wave.open(str(audio), "rb") as handle:
            args.seconds = handle.getnframes() / handle.getframerate()
    else:
        audio = write_fixture(work / "fixture.wav", args.seconds)

    print(
        f"\n  {args.seconds:.0f}s fixture · {args.model} · "
        f"{support.encoder} at {args.height}p{args.fps}\n"
    )

    # 1. Transcription alone.
    alone, segments = transcribe(audio, args.backend, args.model)
    rtf_alone = args.seconds / alone if alone else 0.0
    print(f"  transcription alone      {alone:6.1f}s   RTF {rtf_alone:5.2f}×   {segments} segments")

    # 2. Encoding alone.
    started = time.monotonic()
    subprocess.run(  # noqa: S603
        video_command(support, work / "alone.webm", args.seconds, args.fps, args.height),
        check=False,
        capture_output=True,
    )
    encode_alone = time.monotonic() - started
    print(
        f"  encoding alone           {encode_alone:6.1f}s   "
        f"RTF {args.seconds / encode_alone if encode_alone else 0:5.2f}×"
    )

    # 3. Both at once — the number the whole question is about.
    process = subprocess.Popen(  # noqa: S603
        video_command(support, work / "together.webm", args.seconds, args.fps, args.height),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    together, segments_together = transcribe(audio, args.backend, args.model)
    process.wait(timeout=120)
    rtf_together = args.seconds / together if together else 0.0
    print(
        f"  both at once             {together:6.1f}s   RTF {rtf_together:5.2f}×   "
        f"{segments_together} segments"
    )

    slowdown = (together / alone - 1.0) * 100 if alone else 0.0
    print(f"\n  Transcription is {slowdown:+.0f}% slower with video encoding alongside.")

    # **The guard that makes this script honest.** Whisper is autoregressive: given audio with no
    # speech in it, it emits its no-speech token and stops after a handful of steps, so a synthetic
    # tone decodes in a fraction of the time real speech would and the real-time factor comes out
    # at thirty times what a talk would produce. That number is not wrong, it is measuring the
    # wrong thing — and reporting "both fit" from it would be a confident answer to a question this
    # run did not ask. So a run that produced no transcript refuses to conclude anything.
    if segments_together == 0:
        print(
            "\n  NO CONCLUSION. The audio produced no transcript, so the model stopped almost\n"
            "  immediately instead of decoding — Whisper short-circuits on anything that is not\n"
            "  speech, whatever the filters are set to. The figures above measure that\n"
            "  short-circuit, not the contention this script exists to measure.\n\n"
            "  Re-run against a real recording:\n\n"
            "      uv run scripts/measure_capture_cost.py --audio path/to/a/talk.wav\n"
        )
        return 2

    if rtf_together >= 1.0:
        print("  Both fit: live transcription keeps up while recording video.\n")
        return 0

    print(
        "  They do NOT both fit on this machine. Live transcription would fall behind the\n"
        "  speaker and never catch up. Record the window with live transcription OFF and let\n"
        "  the post-capture pass produce the transcript — which is why both switches exist.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
