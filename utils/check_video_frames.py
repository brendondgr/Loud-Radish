#!/usr/bin/env python
"""Report a recording's duration and every frame size it contains (D-080).

    uv run utils/check_video_frames.py data/recordings/<key>/video.webm

A window recording is meant to have exactly one frame size from its first frame to its last: the
fixed canvas. More than one means a resize reached the encoder, which is the fault the canvas
exists to prevent. Checked by decoding the frames themselves with PyAV rather than by asking
`ffprobe` for the stream's declared size: the stream header states the first frame's size and
nothing about the rest, which is exactly how the original fault went unseen.

Exits 0 when the file has exactly one frame size, 1 when it has several or none.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FrameReport:
    """What a recording's frames say about it."""

    path: str
    #: Seconds from the first decoded frame to the last, from the frames' own timestamps.
    duration_s: float = 0.0
    frames: int = 0
    #: How many frames had each ``(width, height)``.
    sizes: Counter = field(default_factory=Counter)
    error: str = ""

    @property
    def steady(self) -> bool:
        """Whether every frame had the same size."""
        return not self.error and len(self.sizes) == 1

    def describe(self) -> str:
        if self.error:
            return f"{self.path}: could not be read ({self.error})"
        sizes = ", ".join(f"{w}x{h} ({n} frames)" for (w, h), n in self.sizes.most_common())
        verdict = "one frame size throughout" if self.steady else "SIZE CHANGED mid-recording"
        return f"{self.path}: {self.duration_s:.1f} s, {self.frames} frames, {sizes} — {verdict}"


def inspect(path: str | Path) -> FrameReport:
    """Decode every video frame of ``path`` and tally its sizes."""
    import av  # Imported here so `--help` works without the media stack.

    report = FrameReport(path=str(path))
    first = last = None
    try:
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            for frame in container.decode(stream):
                report.frames += 1
                report.sizes[(frame.width, frame.height)] += 1
                if frame.time is not None:
                    first = frame.time if first is None else first
                    last = frame.time
    except (av.error.FFmpegError, IndexError, OSError) as exc:
        # A file cut off mid-cluster still decodes up to the cut, so a partial count is kept; only
        # a file with no readable frame at all is reported as unreadable.
        if not report.frames:
            report.error = str(exc) or type(exc).__name__
    if first is not None and last is not None:
        report.duration_s = max(0.0, last - first)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="video files to inspect")
    args = parser.parse_args(argv)

    ok = True
    for path in args.paths:
        report = inspect(path)
        print(report.describe())
        ok = ok and report.steady
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
