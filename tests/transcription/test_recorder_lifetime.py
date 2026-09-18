"""A video recorder that cannot outlive the server (D-074).

On 2026-09-18 two recorders survived the server that started them, wrote nothing for an hour, and
left their files without an index. The recorder here is a stand-in that does what ``gst-launch-1.0
-e`` does with ``SIGINT`` — finishes its file and exits — so the tests need no GStreamer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from app.services.capture import lifetime
from app.services.capture.lifetime import finalise_orphans, find_orphans, guarded, spawn_recorder

BACKEND = Path(__file__).resolve().parents[2] / "web" / "backend"

needs_setpriv = pytest.mark.skipif(shutil.which("setpriv") is None, reason="needs util-linux")

#: Finishes its "file" on SIGINT, as `gst-launch-1.0 -e` does, then exits.
FAKE_RECORDER = textwrap.dedent(
    """
    import signal, sys, time
    from pathlib import Path
    target = Path(sys.argv[-1].removeprefix("location="))
    def finish(*_):
        target.with_suffix(".finalised").write_text("indexed")
        sys.exit(0)
    signal.signal(signal.SIGINT, finish)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("frames")
    while True:
        time.sleep(0.05)
    """
)


def wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def fake_recorder(tmp_path: Path) -> Path:
    script = tmp_path / "fake_recorder.py"
    script.write_text(FAKE_RECORDER)
    return script


# -- the death signal ------------------------------------------------------------------


@needs_setpriv
def test_a_recorder_is_started_with_a_parent_death_signal() -> None:
    args = guarded(["gst-launch-1.0", "-e", "fakesrc", "!", "fakesink"])
    assert args[1:4] == ["--pdeathsig", "INT", "--"]
    assert args[4:] == ["gst-launch-1.0", "-e", "fakesrc", "!", "fakesink"]


@needs_setpriv
def test_a_recorder_started_from_a_short_lived_thread_outlives_the_thread() -> None:
    """The watcher thread that restarts a stalled capture exits; its recorder must not."""
    box: dict[str, subprocess.Popen] = {}
    thread = threading.Thread(target=lambda: box.update(p=spawn_recorder(["sleep", "30"])))
    thread.start()
    thread.join()
    try:
        time.sleep(0.3)
        assert box["p"].poll() is None
    finally:
        box["p"].kill()
        box["p"].wait()


@needs_setpriv
def test_when_the_server_dies_the_recorder_finalises_its_file(tmp_path, fake_recorder) -> None:
    output = tmp_path / "recordings" / "x" / "video.webm"
    server = textwrap.dedent(
        f"""
        import os, sys, time
        sys.path.insert(0, {str(BACKEND)!r})
        from app.services.capture.lifetime import spawn_recorder
        spawn_recorder([{sys.executable!r}, {str(fake_recorder)!r}, "location={output}"],
                       start_new_session=True)
        time.sleep(1.0)
        os._exit(1)  # the way a GPU fault ends a process: no teardown runs
        """
    )
    subprocess.run([sys.executable, "-c", server], check=False, timeout=20)

    assert wait_until(output.with_suffix(".finalised").exists)


# -- the backstop at start-up ----------------------------------------------------------


def start_orphan(tmp_path: Path, fake_recorder: Path, location: str) -> int:
    """A process named gst-launch-1.0 whose parent has already gone — a real orphan."""
    pid_file = tmp_path / f"pid-{abs(hash(location))}"
    subprocess.run(
        [
            "bash",
            "-c",
            f"(exec -a gst-launch-1.0 {sys.executable} {fake_recorder} location={location} "
            f"& echo $! > {pid_file}) &",
        ],
        cwd=tmp_path,
        check=True,
    )
    assert wait_until(lambda: pid_file.exists() and pid_file.read_text().strip())
    return int(pid_file.read_text())


def test_a_recorder_writing_into_the_recordings_folder_is_found(tmp_path, fake_recorder) -> None:
    pid = start_orphan(tmp_path, fake_recorder, "recordings/a/video.webm")
    try:
        assert wait_until(lambda: (tmp_path / "recordings/a/video.webm").exists())
        found = find_orphans(tmp_path / "recordings")
        assert [(o.pid, o.output.name) for o in found] == [(pid, "video.webm")]
    finally:
        os.kill(pid, 9)


def test_a_pipeline_writing_anywhere_else_is_left_alone(tmp_path, fake_recorder) -> None:
    pid = start_orphan(tmp_path, fake_recorder, "elsewhere/video.webm")
    try:
        assert wait_until(lambda: (tmp_path / "elsewhere/video.webm").exists())
        assert find_orphans(tmp_path / "recordings") == []
    finally:
        os.kill(pid, 9)


def test_a_recorder_this_server_started_is_not_an_orphan(tmp_path, fake_recorder) -> None:
    process = subprocess.Popen(
        ["bash", "-c", f"exec -a gst-launch-1.0 {sys.executable} {fake_recorder} "
         "location=recordings/b/video.webm"],
        cwd=tmp_path,
    )
    try:
        assert wait_until(lambda: (tmp_path / "recordings/b/video.webm").exists())
        # `exec -a` sets argv[0] in a child of bash, which is this process's own child.
        assert all(o.pid != process.pid for o in find_orphans(tmp_path / "recordings"))
    finally:
        process.kill()
        process.wait()


def test_start_up_asks_each_orphan_to_finalise(tmp_path, fake_recorder) -> None:
    pid = start_orphan(tmp_path, fake_recorder, "recordings/c/video.webm")
    output = tmp_path / "recordings/c/video.webm"
    assert wait_until(output.exists)

    finalised = finalise_orphans(tmp_path / "recordings", wait_s=5.0)

    assert [o.pid for o in finalised] == [pid]
    assert output.with_suffix(".finalised").read_text() == "indexed"
    assert not lifetime._running(pid)
