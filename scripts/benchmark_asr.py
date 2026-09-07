#!/usr/bin/env python3
"""Measure the speech models on *this* machine, so a default can be chosen from data.

`docs/checklist.md` has carried "Default ASR model and compute device — depends entirely on the
user's hardware" since the architecture document was written, with the note that it cannot be
decided from published benchmarks. This is what decides it: every combination of model, device and
precision, over the same audio, reporting how much faster than real time each one runs and how
much they disagree about what was said.

**Speed alone would pick `tiny` every time.** So accuracy is reported next to it, as agreement with
the largest model that was run — not because that model is right, but because it is the best
available opinion and a smaller model that disagrees with it constantly is one to be suspicious of.

Run it over a recording of the sort of thing you actually record:

    uv run --no-sync python scripts/benchmark_asr.py data/audio/seminar-speech-real.wav

`--no-sync` matters on an AMD machine: a plain `uv run` replaces the ROCm build of CTranslate2 with
the PyPI one, and the models then refuse to load on the GPU (see `docs/workflow.md`).
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "web" / "backend"))

DEFAULT_MODELS = ("tiny", "base", "small")
DEFAULT_PRECISIONS = ("int8", "float16")

#: How the worker hands one result back to the parent.
WORKER_MARKER = "RESULT "

#: Generous: a cold `large-v3` load over a slow disk is minutes. A hang is a result too.
WORKER_TIMEOUT_S = 900


@dataclass
class Result:
    """One (model, device, precision) measured over one clip."""

    model: str
    device: str
    precision: str
    load_seconds: float = 0.0
    transcribe_seconds: float = 0.0
    audio_seconds: float = 0.0
    words: int = 0
    agreement: float = 0.0
    #: Kept so the models can be compared with each other, not printed.
    text: str = ""
    error: str = ""

    @property
    def realtime_factor(self) -> float:
        """How many seconds of audio it handles per second of wall clock."""
        if self.transcribe_seconds <= 0:
            return 0.0
        return self.audio_seconds / self.transcribe_seconds

    @property
    def ok(self) -> bool:
        return not self.error


def audio_duration(path: Path) -> float:
    with wave.open(str(path)) as handle:
        return handle.getnframes() / float(handle.getframerate())


def measure(path: Path, model: str, device: str, precision: str, warmup: bool = True) -> Result:
    """Load one configuration, transcribe the clip, and time both halves separately.

    The load is timed apart from the transcription because they are paid at different moments: the
    load happens once when the application starts, the transcription happens on every recording.
    A model that loads slowly and runs quickly is a fine choice for a long talk and a poor one for
    dictation.
    """
    import asyncio

    from app.config.schema import AsrConfig
    from app.services.asr.lifecycle import AsrLifecycle
    from app.services.recording.batch import transcribe_file

    result = Result(model=model, device=device, precision=precision)
    result.audio_seconds = audio_duration(path)

    config = AsrConfig(backend="faster-whisper", model=model, device=device, precision=precision)
    asr = AsrLifecycle(config)
    try:
        started = time.monotonic()
        asyncio.run(asr.load(config))
        result.load_seconds = time.monotonic() - started

        # **A warm pass first, and it is not a formality.** Without one, a GPU row includes kernel
        # compilation and reads about a third of the model's real throughput — which is exactly the
        # discrepancy that showed up between this script's first output and the figures already in
        # `docs/deployment.md`. The number wanted here is inference speed during a recording, and
        # by then the model has been warm for a while.
        if warmup:
            transcribe_file(path, transcribe=asr.transcribe)

        started = time.monotonic()
        segments = transcribe_file(path, transcribe=asr.transcribe)
        result.transcribe_seconds = time.monotonic() - started
        result.text = " ".join(segment.text.strip() for segment in segments).strip()
        result.words = len(result.text.split())
    except Exception as exc:  # noqa: BLE001 - an unsupported combination is a result, not a crash
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            asyncio.run(asr.unload())
        except Exception:  # noqa: BLE001, S110 - nothing useful to do while tearing down
            pass
    return result


def measure_isolated(
    path: Path, model: str, device: str, precision: str, warmup: bool = True
) -> Result:
    """Measure one combination in a **fresh process**, and never crash this one.

    Two reasons, both found the hard way.

    **The results were contaminated.** Loading several models one after another in a single process
    gave nonsense on the GPU — the same combination reported 55 words on one run and 0 on the next,
    and `large-v3-turbo` dropped from 12x to 0.9x. Whatever ROCm keeps between loads, it is not
    reset by unloading the model, so each measurement now starts from nothing.

    **One combination aborts the process.** `small` at int8 on an AMD GPU dies with a GPU memory
    fault rather than an exception (D-053). In-process, that ends the whole benchmark on whichever
    row it reaches. In a subprocess it is one missing row.
    """
    done = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(Path(__file__).resolve()),
            str(path),
            "--worker",
            model,
            device,
            precision,
            *([] if warmup else ["--no-warmup"]),
        ],
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT_S,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "web" / "backend")},
        check=False,
    )
    for line in done.stdout.splitlines():
        if line.startswith(WORKER_MARKER):
            return Result(**json.loads(line[len(WORKER_MARKER) :]))
    tail = (done.stderr or done.stdout).strip().splitlines()
    reason = tail[-1][:120] if tail else f"the worker exited with {done.returncode}"
    return Result(model=model, device=device, precision=precision, error=reason)


def score_agreement(results: list[Result]) -> None:
    """Set each result's agreement with a **median-length** transcript, as a 0-1 ratio.

    Not "agreement with the largest model", which was the first attempt and was wrong here: on this
    machine the largest model on the GPU returned 99 words for a 54-word clip, so every other row
    was scored against a hallucination and the whole column read 39%. The median length is a crude
    proxy for "the transcript that is not obviously broken", and crude is the right ambition — this
    column exists to make a row that disagrees with everything else *visible*, not to grade models
    against truth. Nothing here knows what was actually said.
    """
    ordered = sorted(
        (entry for entry in results if entry.ok and entry.text and entry.words),
        key=lambda entry: entry.words,
    )
    if not ordered:
        return
    reference = ordered[len(ordered) // 2]
    for entry in ordered:
        entry.agreement = difflib.SequenceMatcher(
            None, reference.text.lower().split(), entry.text.lower().split()
        ).ratio()


def report(results: list[Result], reference: str) -> str:
    """A table someone can act on."""
    lines = [
        "",
        f"  Measured over {reference}",
        "",
        f"  {'model':<8} {'device':<7} {'precision':<10} {'load':>7} {'speed':>9} "
        f"{'words':>7} {'agree':>7}",
        f"  {'-' * 8} {'-' * 7} {'-' * 10} {'-' * 7} {'-' * 9} {'-' * 7} {'-' * 7}",
    ]
    for entry in results:
        if not entry.ok:
            lines.append(
                f"  {entry.model:<8} {entry.device:<7} {entry.precision:<10} "
                f"{'—':>7} {'unavailable':>9}   {entry.error[:44]}"
            )
            continue
        lines.append(
            f"  {entry.model:<8} {entry.device:<7} {entry.precision:<10} "
            f"{entry.load_seconds:6.1f}s {entry.realtime_factor:8.1f}x "
            f"{entry.words:7d} {entry.agreement * 100:6.0f}%"
        )
    lines.append("")
    lines.append("  speed = seconds of audio handled per second of wall clock.")
    lines.append("  agree = word overlap with the median-length transcript, not with the truth.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="A 16 kHz mono WAV to measure over.")
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--devices", nargs="+", default=["cpu", "cuda"])
    parser.add_argument("--precisions", nargs="+", default=list(DEFAULT_PRECISIONS))
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Time the first pass, including kernel compilation. Usually not what you want.",
    )
    parser.add_argument("--json", type=Path, help="Also write the raw numbers here.")
    parser.add_argument(
        "--worker",
        nargs=3,
        metavar=("MODEL", "DEVICE", "PRECISION"),
        help="Internal: measure one combination and print it. Not for direct use.",
    )
    args = parser.parse_args(argv)

    if args.worker:
        model, device, precision = args.worker
        one = measure(args.audio, model, device, precision, warmup=not args.no_warmup)
        print(WORKER_MARKER + json.dumps(asdict(one)))
        return 0

    if not args.audio.is_file():
        print(f"No such file: {args.audio}", file=sys.stderr)
        return 1

    # **The same repair `app.py` performs on launch.** Any `uv run` synchronises against the
    # lockfile, which says PyPI, and so replaces the ROCm build of CTranslate2 with the CPU/CUDA
    # one — after which every GPU row of this table reads "the GPU runtime rejected loading". That
    # happened twice while writing this script before the cause was obvious.
    from app.services.asr.acceleration import repair_kept_wheel

    repaired = repair_kept_wheel()
    if repaired:
        print(f"  {repaired}")

    results: list[Result] = []
    for model in args.models:
        for device in args.devices:
            for precision in args.precisions:
                print(f"  measuring {model}/{device}/{precision}…", flush=True)
                results.append(
                    measure_isolated(
                        args.audio, model, device, precision, warmup=not args.no_warmup
                    )
                )

    score_agreement(results)
    print(report(results, str(args.audio)))

    if args.json:
        args.json.write_text(
            json.dumps([asdict(entry) for entry in results], indent=2) + "\n", encoding="utf-8"
        )
        print(f"  raw numbers: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
