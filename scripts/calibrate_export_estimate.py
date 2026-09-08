#!/usr/bin/env python3
"""Check the export estimator against what an encoder actually produces.

The estimator in `services/export/estimate.py` predicts a size and a duration for every preset
before anything is encoded. Its constants were fitted to one recording on one machine, and both
halves of that matter: content decides the size and the CPU decides the time. This script re-derives
them anywhere.

It takes a slice out of a real recording, encodes it at every preset, and prints the prediction
beside the measurement — scaled to the whole recording, because that is the figure the export window
shows and therefore the one worth being right about.

    uv run scripts/calibrate_export_estimate.py data/recordings/<key>/video-with-audio.webm

A prediction is doing its job when the measurement falls inside the printed range. One that does not
means `H264_EFFICIENCY_AT_REF` (for size) or `ENCODE_S_PER_MPX` (for time) wants re-fitting to this
machine and this kind of content; both are single constants and the arithmetic to re-fit them is in
that module's docstring.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import prepare  # noqa: E402

prepare()

from app.services.export.encode import build_command  # noqa: E402
from app.services.export.estimate import estimate  # noqa: E402
from app.services.export.presets import PRESETS  # noqa: E402
from app.services.export.profile import probe  # noqa: E402


def megabytes(value: float) -> str:
    return f"{value / 1e6:8.1f} MB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path, help="A finished video to calibrate against")
    parser.add_argument(
        "--seconds", type=float, default=120.0, help="How much of it to encode (default 120)"
    )
    parser.add_argument("--start", type=float, default=0.0, help="Where in it to start (default 0)")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print("ffmpeg is not installed, so there is nothing to calibrate against.")
        return 1

    whole = probe(args.recording)
    if not whole.readable:
        print(f"Could not read {args.recording}: {whole.problem}")
        return 1

    print(
        f"Source: {whole.width}x{whole.height} at {whole.frame_rate:g} fps, "
        f"{whole.duration_s:.0f}s, {whole.size_bytes / 1e6:.1f} MB "
        f"({whole.video_codec}/{whole.audio_codec or 'silent'})\n"
    )

    with tempfile.TemporaryDirectory(prefix="export-calibration-") as scratch:
        work = Path(scratch)
        slice_path = work / f"slice{args.recording.suffix}"
        subprocess.run(  # noqa: S603 - fixed binary, argv built here, no shell
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{args.start:.3f}", "-t", f"{args.seconds:.3f}",
                "-i", str(args.recording), "-c", "copy", str(slice_path),
            ],
            check=True,
        )  # fmt: skip
        sample = probe(slice_path)
        if not sample.readable:
            print(f"The slice could not be read: {sample.problem}")
            return 1

        # Predictions are made against the *whole* recording, and measurements against the slice,
        # so the slice is scaled up before the two are compared. Comparing them at slice scale
        # would hide any error that only shows over an hour, which is the length that matters.
        scale = whole.duration_s / sample.duration_s

        print(f"{'preset':10} {'predicted':>11}  {'range':>19}  {'measured':>11}  {'in?':>4}  time")
        print("-" * 74)
        inside = 0
        for plan in PRESETS:
            predicted = estimate(plan, whole)
            output = work / f"{plan.id}.{plan.extension(sample)}"
            started = time.monotonic()
            subprocess.run(  # noqa: S603 - argv built from a fixed vocabulary
                build_command(replace(plan), sample, output), check=True, capture_output=True
            )
            elapsed = time.monotonic() - started
            measured = output.stat().st_size * scale

            # **A copy is not judged against a slice.** Its predicted size is the file's own, read
            # off disk, and scaling a dense two-minute excerpt up to an hour overstates a recording
            # whose density varies — this one is 29 per cent real footage and 71 per cent held
            # frame, and the excerpt came from the dense part. There is nothing to check: the
            # number is the file.
            ok = predicted.exact or predicted.low_bytes <= measured <= predicted.high_bytes
            inside += int(ok)
            print(
                f"{plan.id:10} {megabytes(predicted.bytes)}  "
                f"{megabytes(predicted.low_bytes)}–{megabytes(predicted.high_bytes)}  "
                f"{megabytes(measured)}  {'—' if predicted.exact else ('yes' if ok else 'NO'):>4}  "
                f"{predicted.seconds:5.0f}s predicted / {elapsed * scale:5.0f}s measured"
            )

    print(f"\n{inside} of {len(PRESETS)} predictions contained the measurement.")
    return 0 if inside == len(PRESETS) else 2


if __name__ == "__main__":
    raise SystemExit(main())
