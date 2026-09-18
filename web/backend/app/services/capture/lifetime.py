"""A video recorder that cannot outlive the server (D-074).

The recorder is started in its own session on purpose — a Ctrl+C in the server's terminal must not
end it before the session has finished with it (see ``recorder.py``). The price, found on
2026-09-18, is that nothing ends it when the server dies. Both recorders from that day's crashes
were still running an hour later, writing nothing, their ``video.webm`` never finalised: no
duration, no index, not seekable.

Two measures, the first preventive and the second a backstop:

* **Every recorder is started with a parent-death signal.** ``setpriv --pdeathsig INT`` sets
  ``PR_SET_PDEATHSIG`` and then execs ``gst-launch-1.0``, so the recorder receives ``SIGINT`` the
  moment the server dies, and ``-e`` turns that into end-of-stream: the file is finalised exactly as
  an ordinary stop would finalise it. The kernel ties that signal to the *thread* that forked the
  child, and a recorder can be restarted from a short-lived watcher thread, so every recorder is
  forked by :class:`_Spawner`'s one thread, which lives as long as the process.
* **Start-up finalises what an older server left behind.** :func:`finalise_orphans` finds
  ``gst-launch-1.0`` processes writing into this installation's recordings directory — at start-up
  there can be no legitimate one — and sends each the same ``SIGINT``.

``setpriv`` is util-linux, present on essentially every Linux desktop. Without it recorders start
exactly as before, and only the backstop applies.
"""

from __future__ import annotations

import errno
import logging
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RECORDER_BINARY = "gst-launch-1.0"
#: Seconds an orphan gets to write its index after ``SIGINT`` before it is killed. The same order
#: as an ordinary stop's finalise timeout: the work is identical.
ORPHAN_FINALISE_S = 15.0


def guarded(args: list[str]) -> list[str]:
    """``args`` prefixed so the process receives ``SIGINT`` when its parent dies."""
    setpriv = shutil.which("setpriv")
    if setpriv is None:
        return list(args)
    return [setpriv, "--pdeathsig", "INT", "--", *args]


class _Spawner:
    """Forks every recorder from one thread that lives as long as the server."""

    def __init__(self) -> None:
        self._requests: queue.Queue[tuple[list[str], dict[str, Any], dict[str, Any]]] = (
            queue.Queue()
        )
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def popen(self, args: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
        """``subprocess.Popen(guarded(args), **kwargs)``, forked from the spawner thread."""
        self._ensure_thread()
        box: dict[str, Any] = {"done": threading.Event()}
        self._requests.put((guarded(args), kwargs, box))
        box["done"].wait()
        if "error" in box:
            raise box["error"]
        return box["process"]

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="recorder-spawner", daemon=True
                )
                self._thread.start()

    def _run(self) -> None:
        while True:
            args, kwargs, box = self._requests.get()
            try:
                box["process"] = subprocess.Popen(args, **kwargs)  # noqa: S603 - built by caller
            except BaseException as exc:  # noqa: BLE001 - handed back to the caller's thread
                box["error"] = exc
            finally:
                box["done"].set()


_SPAWNER = _Spawner()


def spawn_recorder(args: list[str], **kwargs: Any) -> subprocess.Popen[Any]:
    """Start a recorder that is sent ``SIGINT`` if this process dies.

    A missing binary still raises :class:`FileNotFoundError` here, as a bare ``Popen`` would.
    Behind ``setpriv`` it would otherwise start, fail to exec, and surface later as an exit code.
    """
    if args and shutil.which(args[0]) is None:
        raise FileNotFoundError(errno.ENOENT, "No such executable", args[0])
    return _SPAWNER.popen(args, **kwargs)


# -- the backstop ----------------------------------------------------------------------


@dataclass(frozen=True)
class Orphan:
    """A recorder left running by a server that is no longer here."""

    pid: int
    output: Path


def find_orphans(recordings_dir: Path, proc: Path = Path("/proc")) -> list[Orphan]:
    """Recorders writing into ``recordings_dir`` that this process did not start.

    Matched on what the process is *doing* — a ``filesink location=`` inside the recordings
    directory, resolved against the process's own working directory — rather than on its name
    alone, so another application's GStreamer pipeline is never touched.
    """
    root = recordings_dir.resolve()
    me = os.getpid()
    uid = os.getuid()
    found: list[Orphan] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if not argv or Path(os.fsdecode(argv[0])).name != RECORDER_BINARY:
                continue
            if _parent_of(entry) == me:
                continue
            cwd = Path(os.readlink(entry / "cwd"))
        except (OSError, ValueError):
            continue
        for arg in argv:
            text = os.fsdecode(arg)
            if not text.startswith("location="):
                continue
            output = (cwd / text.removeprefix("location=")).resolve()
            if output.is_relative_to(root) and output.suffix != ".jpg":
                found.append(Orphan(pid=int(entry.name), output=output))
                break
    return found


def finalise_orphans(recordings_dir: Path, wait_s: float = ORPHAN_FINALISE_S) -> list[Orphan]:
    """Ask every orphaned recorder to finish its file, and kill any that will not."""
    orphans = find_orphans(recordings_dir)
    for orphan in orphans:
        logger.warning(
            "A video recorder (pid %d) was left running by a server that exited without stopping "
            "it; finalising %s",
            orphan.pid,
            orphan.output,
        )
        _signal(orphan.pid, signal.SIGINT)

    deadline = time.monotonic() + wait_s
    for orphan in orphans:
        while _running(orphan.pid) and time.monotonic() < deadline:
            time.sleep(0.2)
        if _running(orphan.pid):
            logger.warning("Recorder %d did not finalise in %.0f s; killing it", orphan.pid, wait_s)
            _signal(orphan.pid, signal.SIGKILL)
    return orphans


def finalise_orphans_in_background(recordings_dir: Path) -> threading.Thread:
    """Run :func:`finalise_orphans` off the start-up path; finalising can take seconds."""
    thread = threading.Thread(
        target=_finalise_quietly, args=(recordings_dir,), name="orphan-recorders", daemon=True
    )
    thread.start()
    return thread


def _finalise_quietly(recordings_dir: Path) -> None:
    try:
        finalise_orphans(recordings_dir)
    except Exception:  # noqa: BLE001 - a backstop must never stop the server starting
        logger.exception("Could not check for orphaned video recorders")


def _parent_of(entry: Path) -> int:
    stat = (entry / "stat").read_text()
    return int(stat.rsplit(")", 1)[1].split()[1])


def _running(pid: int) -> bool:
    """Whether ``pid`` exists and is not a zombie."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _signal(pid: int, sig: signal.Signals) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
