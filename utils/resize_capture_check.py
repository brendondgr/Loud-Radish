#!/usr/bin/env python
"""Resize a window mid-recording, automatically, and check the recording survived it (D-080).

    uv run utils/resize_capture_check.py                  # 20 s, a resize every 2.5 s, 1080p
    uv run utils/resize_capture_check.py --seconds 60 --canvas 720p --keep out.webm
    uv run utils/resize_capture_check.py --without-canvas # the pipeline as it was before D-080

The fault: resizing or moving a window during a recording stalled the video and left a file with
no duration. When a window is resized the compositor renegotiates its screen-cast stream, and
``pipewiresrc`` passes the new size downstream as a caps change — which, with no fixed-size stage
in the way, reached an encoder and a muxer that cannot change resolution mid-stream.

Reproducing that through the real portal needs a person to answer the compositor's dialog and drag
a window around, so this reproduces the part that failed instead:

1. **The application's own pipeline**, built by ``capture.pipeline.build`` exactly as a window
   recording builds it.
2. **Only its source is replaced**: ``pipewiresrc`` becomes a live test pattern whose caps are
   changed on a timer, which is exactly what ``pipewiresrc`` emits when the compositor resizes the
   stream. It runs in the system Python, which has GStreamer's introspection bindings — the
   project's virtualenv deliberately does not (D-022).
3. **The verdict.** No pipeline error, a file about as long as the run, and **one frame size**
   from the first frame to the last (``check_video_frames``).

**Why not a fake PipeWire window.** That was the first version: a test pattern published through
``pipewiresink``. It does not renegotiate — a consumer kept reading the first size while the frames
changed under it, so every recording came out garbled with or without the fix, and it fails outright
when a frame shrinks to around 500x500. A real compositor does renegotiate; that one link, and
``pipewiresrc`` itself on DMA-BUF, is what a real-desktop check still covers.

Inside a toolbox or distrobox container, where GStreamer lives on the host, the system Python is
run through ``flatpak-spawn --host`` automatically.

Exits 0 when the recording survived every resize, 1 when it did not, 2 when it could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The backend's package, and this script's siblings. Never the repository root itself: it holds an
# `app.py` that would shadow the backend's `app` package.
sys.path.insert(0, str(HERE.parent / "web" / "backend"))
sys.path.insert(0, str(HERE))

from check_video_frames import FrameReport, inspect  # noqa: E402

#: Sizes the "window" cycles through. Different aspect ratios on purpose — a canvas that only
#: survived same-shaped resizes would not survive a person dragging a corner — and odd dimensions,
#: which the encoder cannot take and the canvas must absorb.
SIZES = (
    (800, 600),
    (1281, 721),
    (480, 900),
    (1200, 500),
    (2560, 1440),
    (641, 359),
    (1366, 768),
)

#: Background of the test pattern. Anything but black, so the canvas's letterbox bars — which are
#: black — can be told apart from the picture in a frame grab.
BACKGROUND = "0xff2d5f8b"

#: The rig, run under the system Python. Reads the launch line as JSON, swaps the source, resizes it
#: on a timer, then sends end-of-stream exactly as `gst-launch-1.0 -e` does on SIGINT, so the file
#: is finalised the way a stopped recording's is. One line per event on stdout.
RIG = r"""
import json, sys, gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib
Gst.init(None)
args = json.loads(sys.argv[1])
every_ms, seconds, background = int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
sizes = [tuple(map(int, s.split("x"))) for s in sys.argv[5].split(",")]
caps = "video/x-raw,format=BGRx,width={},height={},framerate=30/1,pixel-aspect-ratio=1/1"

start = args.index("pipewiresrc")
end = args.index("!", start)
source = [
    "videotestsrc", "is-live=true", "pattern=ball", f"background-color={background}",
    "!", "capsfilter", "name=window", "caps=" + caps.format(*sizes[0]),
]
launch = " ".join(source + args[end:])
pipeline = Gst.parse_launch(launch)
window = pipeline.get_by_name("window")
loop = GLib.MainLoop()
state = {"i": 0, "code": 0}

def resize():
    state["i"] += 1
    w, h = sizes[state["i"] % len(sizes)]
    window.set_property("caps", Gst.Caps.from_string(caps.format(w, h)))
    print(f"resize {w}x{h}", flush=True)
    return True

def finish():
    print("eos-sent", flush=True)
    pipeline.send_event(Gst.Event.new_eos())
    # A pipeline that cannot finalise within this long would leave the file without an index.
    GLib.timeout_add_seconds(10, lambda: (print("eos-timeout", flush=True), loop.quit()))
    return False

def on_message(_bus, message):
    if message.type == Gst.MessageType.ERROR:
        error, debug = message.parse_error()
        detail = (debug or "").replace(chr(10), " ")[-300:]
        print(f"error {message.src.get_name()}: {error.message} ({detail})", flush=True)
        state["code"] = 1
        loop.quit()
    elif message.type == Gst.MessageType.EOS:
        print("eos", flush=True)
        loop.quit()

bus = pipeline.get_bus()
bus.add_signal_watch()
bus.connect("message", on_message)
pipeline.set_state(Gst.State.PLAYING)
print("playing", flush=True)
GLib.timeout_add(every_ms, resize)
GLib.timeout_add(int(seconds * 1000), finish)
loop.run()
pipeline.set_state(Gst.State.NULL)
sys.exit(state["code"])
"""

#: Asks a Python whether it has GStreamer's introspection bindings.
_HAS_GST = "import gi; gi.require_version('Gst', '1.0'); from gi.repository import Gst"


def _in_container() -> bool:
    return Path("/run/.containerenv").exists() and shutil.which("flatpak-spawn") is not None


def system_python() -> list[str]:
    """A Python with GStreamer's bindings, for the rig.

    **Never the one running this script.** `uv run` puts the virtualenv's interpreter first on
    ``PATH``, and it deliberately has no system introspection (D-022), so ``python3`` alone finds
    the one Python that cannot run the rig. The system interpreter is asked by its full path,
    locally first and then on the host.
    """
    candidates = [["/usr/bin/python3"]]
    if _in_container():
        candidates.append(
            ["flatpak-spawn", "--host", f"--directory={os.getcwd()}", "/usr/bin/python3"]
        )
    for candidate in candidates:
        try:
            probe = subprocess.run(
                [*candidate, "-c", _HAS_GST], capture_output=True, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    raise RuntimeError(
        "no Python with GStreamer's introspection bindings was found (install python3-gobject "
        "and the GStreamer base and good plugins on the system)"
    )


def without_canvas(args: list[str]) -> list[str]:
    """``args`` with the canvas stage removed: the pipeline as it was before D-080."""
    start = args.index("videoscale")
    if args[start + 1] != "add-borders=true" or not args[start + 3].startswith("video/x-raw,width"):
        raise RuntimeError("could not find the canvas stage to remove")
    return args[:start] + args[start + 5 :]


@dataclass
class Result:
    """Everything the check observed."""

    seconds: float
    report: FrameReport
    resizes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    finalised: bool = False

    @property
    def problems(self) -> list[str]:
        found = [f"the pipeline failed: {error}" for error in self.errors]
        if not self.finalised and not self.errors:
            found.append("the recording was not finalised (no end-of-stream)")
        if not self.report.steady:
            found.append("the frame size changed, or no frame could be read")
        # Generous on the short side: the pipeline takes a moment to start. A file far shorter
        # than the run lost part of it.
        if self.report.duration_s < self.seconds * 0.85:
            found.append(
                f"the video is {self.report.duration_s:.1f} s long for a {self.seconds:.0f} s run"
            )
        return found


def run(
    seconds: float, every_s: float, canvas_name: str, output: Path, *, canvas: bool = True
) -> Result:
    from app.services.capture.pipeline import build
    from app.services.capture.probe import CaptureSupport

    support = CaptureSupport(
        available=True, encoder="vp8enc", muxer="webmmux", extension="webm", preview=True
    )
    spec = build(
        support,
        node_id=0,
        fd=-1,
        video_path=str(output),
        preview_path=str(output.with_suffix(".jpg")),
        canvas_name=canvas_name,
    )
    args = spec.args if canvas else without_canvas(spec.args)
    sizes = ",".join(f"{w}x{h}" for w, h in SIZES)

    completed = subprocess.run(
        [
            *system_python(),
            "-c",
            RIG,
            json.dumps(args),
            str(int(every_s * 1000)),
            str(seconds),
            BACKGROUND,
            sizes,
        ],
        capture_output=True,
        text=True,
        timeout=seconds + 60,
        check=False,
    )
    result = Result(seconds=seconds, report=inspect(output))
    for line in completed.stdout.splitlines():
        if line.startswith("resize "):
            result.resizes.append(line.removeprefix("resize "))
        elif line.startswith("error "):
            result.errors.append(line.removeprefix("error "))
        elif line == "eos":
            result.finalised = True
    if completed.returncode not in (0, 1) and not result.errors:
        result.errors.append((completed.stderr or "the rig exited unexpectedly").strip()[-500:])
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seconds", type=float, default=20.0, help="how long to record")
    parser.add_argument("--every", type=float, default=2.5, help="seconds between resizes")
    parser.add_argument("--canvas", default="1080p", help="720p, 1080p or 1440p")
    parser.add_argument("--keep", type=Path, help="keep the recording at this path")
    parser.add_argument(
        "--without-canvas",
        action="store_true",
        help="remove the canvas stage, reproducing the fault as it was before D-080",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="resize-check-", dir=os.environ.get("TMPDIR")) as tmp:
        output = (args.keep or Path(tmp) / "video.webm").resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        try:
            result = run(
                args.seconds, args.every, args.canvas, output, canvas=not args.without_canvas
            )
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            print(f"Could not run the check: {exc}")
            return 2

        print(f"Resized the window {len(result.resizes)} times: {', '.join(result.resizes)}")
        print(result.report.describe())
        problems = result.problems
        if problems:
            print("FAILED:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        print("PASSED: the recording survived every resize at one fixed size.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
