"""A speech model in another process, supervised from this one (D-071).

:class:`IsolatedAsrBackend` is an ordinary :class:`~.contract.AsrBackend` to everything above it:
the lifecycle, the streaming engine, the batch pass and dictation call ``transcribe`` exactly as
before. Behind it, the model runs in :mod:`.worker`, and three things that used to end the server
now end only that process:

* **It dies.** A GPU memory fault aborts it with ``SIGABRT``. The request that was in flight is
  noticed within :data:`POLL_S`, a new worker loads the same model (about a second on this machine),
  and the request is made once more. The caller sees a slow pass, not a failure.
* **It stops answering.** A pass that takes longer than :data:`HANG_BASE_S` plus
  :data:`HANG_PER_AUDIO_S` per second of audio is treated as a hung GPU: the worker is killed and
  the pass retried in a new one.
* **It keeps dying.** :data:`CRASH_LIMIT` crashes inside :data:`CRASH_WINDOW_S` means the GPU is not
  going to settle, and the model is reloaded on the CPU for the rest of this load. Slower, and
  still transcribing.

Every restart and the fallback are reported through a notice listener, so the interface says what
happened instead of leaving a gap in the transcript unexplained.

One request at a time: the worker is single-threaded, so concurrent callers (a live session and a
dictation) queue on :attr:`_lock`, which is what happened on the GPU anyway.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

import numpy as np

from ...config.schema import AsrConfig
from ...paths import BACKEND_DIR
from ..audio.formats import SAMPLE_RATE
from .contract import (
    AsrBackend,
    AsrCapabilities,
    AsrError,
    AsrLoadError,
    AsrResult,
    AsrUnavailableError,
)
from .registry import build_in_process

logger = logging.getLogger(__name__)

#: How often a waiting request checks that the worker is still alive.
POLL_S = 0.25
#: A pass unanswered for this long, plus :data:`HANG_PER_AUDIO_S` per second of its audio, is hung.
#: Generous on purpose: a CPU pass over a two-minute dictation chunk is legitimately slow, and
#: killing a worker that was merely working throws the work away.
HANG_BASE_S = 120.0
HANG_PER_AUDIO_S = 4.0
#: This many crashes inside :data:`CRASH_WINDOW_S`, and a GPU model is reloaded on the CPU.
CRASH_LIMIT = 3
CRASH_WINDOW_S = 600.0
#: Seconds an unload gives the worker to leave by itself before it is killed.
EXIT_WAIT_S = 5.0

#: ``(kind, detail)``, where kind is ``"restarted"`` or ``"fell-back"``.
NoticeListener = Callable[[str, str], None]

_ERRORS: dict[str, type[AsrError]] = {"load": AsrLoadError, "unavailable": AsrUnavailableError}


class WorkerDied(AsrError):
    """The worker process ended, or stopped answering, during a request."""


class IsolatedAsrBackend(AsrBackend):
    """Runs a backend in :mod:`.worker` and survives the worker's death."""

    def __init__(
        self,
        config: AsrConfig,
        *,
        hang_base_s: float = HANG_BASE_S,
        crash_limit: int = CRASH_LIMIT,
        crash_window_s: float = CRASH_WINDOW_S,
    ) -> None:
        #: What the worker is told to build. Isolation is off *there*, or it would spawn its own.
        self._config = config.model_copy(update={"process_isolation": False})
        # Identity and capabilities before the load, from an unloaded in-process instance: cheap,
        # and what the model list needs without starting anything.
        shadow = build_in_process(self._config)
        self._backend_id = shadow.backend_id
        self._model_id = shadow.model_id
        self._capabilities = shadow.capabilities
        self._hang_base_s = hang_base_s
        self._crash_limit = crash_limit
        self._crash_window_s = crash_window_s

        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._conn: Connection | None = None
        #: Whether a model is meant to be loaded. A worker that died is restarted to honour it.
        self._loaded = False
        self._crashes: deque[float] = deque()
        self._restarts = 0
        self._fell_back = False
        self._listener: NoticeListener | None = None

    # -- identity ------------------------------------------------------------------

    @property
    def backend_id(self) -> str:
        return self._backend_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def capabilities(self) -> AsrCapabilities:
        return self._capabilities

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def worker_pid(self) -> int | None:
        """The worker's process id, or ``None`` when none is running."""
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    @property
    def restarts(self) -> int:
        """How many times a worker has been replaced after dying or hanging."""
        return self._restarts

    @property
    def fell_back(self) -> bool:
        """Whether repeated crashes moved the model onto the CPU."""
        return self._fell_back

    def set_notice_listener(self, listener: NoticeListener | None) -> None:
        """Be told about restarts and the CPU fallback."""
        self._listener = listener

    # -- lifecycle -----------------------------------------------------------------

    def load(self) -> None:
        """Start a worker and load the model in it."""
        with self._lock:
            if self._loaded and self._alive():
                return
            self._start_locked()

    def unload(self) -> None:
        """Ask the worker to leave, and make sure it has. Its exit frees the device memory."""
        with self._lock:
            self._loaded = False
            conn, process = self._conn, self._process
            if conn is not None and process is not None and process.poll() is None:
                try:
                    conn.send(("unload",))
                    process.wait(timeout=EXIT_WAIT_S)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            self._kill_locked()

    # -- transcription -------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> AsrResult:
        """Transcribe in the worker. A worker that dies is replaced and the pass made once more."""
        array = np.ascontiguousarray(audio, dtype=np.float32).ravel()
        limit = self._hang_base_s + HANG_PER_AUDIO_S * array.size / SAMPLE_RATE
        with self._lock:
            if not self._loaded:
                raise AsrLoadError("Transcribe called before the model was loaded")
            try:
                if not self._alive():
                    self._recover_locked(str(self._death()))
                return self._request_locked(("transcribe", array, prompt), limit)
            except WorkerDied as died:
                self._recover_locked(str(died))
            # A second death here is reported rather than retried again: the next pass will find
            # the worker gone and start another, so nothing is left permanently broken.
            return self._request_locked(("transcribe", array, prompt), limit)

    # -- internals -----------------------------------------------------------------

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _start_locked(self) -> None:
        """Spawn a worker and load the configured model in it. Caller holds the lock."""
        self._kill_locked()
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(BACKEND_DIR), env.get("PYTHONPATH", "")) if part
        )
        try:
            self._process = subprocess.Popen(  # noqa: S603 - our own interpreter and module
                # `-P` keeps the working directory off `sys.path`. The server runs from the
                # repository root, where `app.py` would otherwise be found before the package.
                [sys.executable, "-P", "-m", "app.services.asr.worker", str(child.fileno())],
                pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL,
                env=env,
                # Out of the terminal's process group: a Ctrl+C meant for the server must not
                # kill the model mid-pass. The server's exit still ends it, as end-of-file.
                start_new_session=True,
            )
        except OSError as exc:
            parent.close()
            raise AsrLoadError(f"Could not start the speech model's process: {exc}") from exc
        finally:
            # The parent's copy of the child's end must go, or the child's death is never
            # end-of-file here — the socket would still have a writer.
            child.close()
        self._conn = Connection(parent.detach())

        # No time limit on a load: a first run downloads the model, which can take minutes. A
        # worker that dies while loading is still noticed within POLL_S.
        try:
            info: dict[str, Any] = self._request_locked(
                ("load", self._config.model_dump()), limit=None
            )
        except WorkerDied as died:
            self._kill_locked()
            raise AsrLoadError(f"The speech model's process failed while loading: {died}") from died
        except AsrError:
            self._kill_locked()
            raise

        self._model_id = info["model_id"]
        self._capabilities = AsrCapabilities(**info["capabilities"])
        self._loaded = True
        logger.info("Speech model %s running in process %s", self._model_id, self.worker_pid)

    def _request_locked(self, message: tuple[Any, ...], limit: float | None) -> Any:
        """Send one request and wait for its answer, watching the worker while waiting."""
        conn, process = self._conn, self._process
        if conn is None or process is None:
            raise WorkerDied("No speech model process is running.")
        try:
            conn.send(message)
        except (OSError, ValueError) as exc:
            raise self._death() from exc

        deadline = None if limit is None else time.monotonic() + limit
        while True:
            try:
                if conn.poll(POLL_S):
                    reply = conn.recv()
                    break
            except (EOFError, OSError) as exc:
                raise self._death() from exc
            if process.poll() is not None:
                raise self._death()
            if deadline is not None and time.monotonic() > deadline:
                self._kill_locked()
                raise WorkerDied(f"The speech model stopped answering for {limit:.0f} s.")

        if reply[0] == "ok":
            return reply[1]
        _, kind, detail = reply
        raise _ERRORS.get(kind, AsrError)(detail)

    def _death(self) -> WorkerDied:
        """Describe how the worker ended, and forget it."""
        process = self._process
        if process is None:
            return WorkerDied("The speech model's process was not running.")
        try:
            code: int | None = process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            code = None
        self._kill_locked()
        how = _describe_exit(code)
        return WorkerDied(f"The speech model's process ended unexpectedly ({how}).")

    def _recover_locked(self, reason: str) -> None:
        """Replace a dead or hung worker, on the CPU once the GPU has failed too often."""
        now = time.monotonic()
        self._crashes.append(now)
        while self._crashes and now - self._crashes[0] > self._crash_window_s:
            self._crashes.popleft()
        self._restarts += 1

        kind = "restarted"
        if len(self._crashes) >= self._crash_limit and not self._fell_back and self._on_gpu():
            self._config = self._config.model_copy(update={"device": "cpu", "precision": "int8"})
            self._fell_back = True
            kind = "fell-back"
        logger.error(
            "%s Starting a new one%s (%d in the last %.0f min).",
            reason,
            " on the CPU" if kind == "fell-back" else "",
            len(self._crashes),
            self._crash_window_s / 60,
        )
        self._start_locked()
        if self._listener is not None:
            try:
                self._listener(kind, reason)
            except Exception:  # noqa: BLE001 - a broken listener must not fail the pass
                logger.exception("ASR notice listener raised")

    def _on_gpu(self) -> bool:
        return self._capabilities.runs_on == "cuda" or self._config.device == "cuda"

    def _kill_locked(self) -> None:
        """End the worker, if there is one, and close the connection to it."""
        process, conn = self._process, self._conn
        self._process = self._conn = None
        if conn is not None:
            conn.close()
        if process is not None and process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=EXIT_WAIT_S)
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("The speech model's process %s did not exit", process.pid)


def _describe_exit(code: int | None) -> str:
    """``SIGABRT`` rather than ``-6``, with what that usually means here."""
    if code is None:
        return "it did not exit"
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        if -code == signal.SIGABRT:
            return f"{name} — the GPU runtime aborts a process after a memory fault"
        return name
    return f"exit code {code}"
