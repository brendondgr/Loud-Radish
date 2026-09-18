"""The speech model's own process (D-071).

A GPU memory fault is not an exception. The ROCm runtime answers one by aborting the process that
caused it, so nothing in that process gets to run another line — not a handler, not a session
teardown, not the code that finalises a video. On 2026-09-18 that process was the server, and two
recordings ended with it. The fault cannot be caught, but it can be kept out of the process that
matters: the model lives here, and :mod:`.isolated` starts another one if this one dies.

**The protocol** is one request at a time over a socket inherited from the server, using
:class:`multiprocessing.connection.Connection` framing:

* ``("load", config_dict)``           → ``("ok", {"model_id": ..., "capabilities": {...}})``
* ``("transcribe", audio, prompt)``   → ``("ok", AsrResult)``
* ``("unload",)``                     → ``("ok", None)``, and the process exits

A failure the backend raises comes back as ``("error", kind, message)`` and the process keeps
serving. **The server's death is end-of-file on the socket**, so this process never outlives it
and never needs to be found and killed.

Run as ``python -m app.services.asr.worker <fd>``. Not a place for anything but the model: every
import here is paid again on each restart.
"""

from __future__ import annotations

import logging
import os
import resource
import sys
from dataclasses import asdict
from multiprocessing.connection import Connection
from typing import Any

from ...config.schema import AsrConfig
from .contract import AsrBackend, AsrLoadError, AsrUnavailableError
from .registry import build_in_process

logger = logging.getLogger("app.services.asr.worker")


def serve(conn: Connection) -> int:
    """Answer requests until the server goes away or asks this process to stop."""
    backend: AsrBackend | None = None
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            logger.info("The server has gone; the speech model process is exiting")
            return 0

        op = message[0]
        try:
            if op == "load":
                backend = _load(AsrConfig.model_validate(message[1]))
                conn.send(("ok", _describe(backend)))
            elif op == "transcribe":
                if backend is None:
                    raise AsrLoadError("Transcribe was called before a model was loaded")
                conn.send(("ok", backend.transcribe(message[1], message[2])))
            elif op == "unload":
                if backend is not None:
                    backend.unload()
                conn.send(("ok", None))
                return 0
            else:
                conn.send(("error", "protocol", f"Unknown request {op!r}"))
        except (EOFError, OSError):
            return 0
        except Exception as exc:  # noqa: BLE001 - every failure goes back to the server, named
            conn.send(("error", _kind(exc), str(exc) or type(exc).__name__))


def _load(config: AsrConfig) -> AsrBackend:
    backend = build_in_process(config)
    backend.load()
    return backend


def _describe(backend: AsrBackend) -> dict[str, Any]:
    return {"model_id": backend.model_id, "capabilities": asdict(backend.capabilities)}


def _kind(exc: Exception) -> str:
    if isinstance(exc, AsrUnavailableError):
        return "unavailable"
    if isinstance(exc, AsrLoadError):
        return "load"
    return "error"


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "info").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logger.info("Speech model process started (pid %d)", os.getpid())
    _no_core_dumps()
    _restore_gpu_build()
    return serve(Connection(int(argv[1])))


def _no_core_dumps() -> None:
    """Die quickly. A GPU fault aborts this process, and dumping its core — gigabytes of mapped
    device memory handed to systemd-coredump — is time the server spends waiting to start the
    next one, while audio queues. The kernel log and the server log already say what happened."""
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError):
        logger.debug("Could not switch off core dumps", exc_info=True)


def _restore_gpu_build() -> None:
    """Put the ROCm CTranslate2 back if a ``uv run`` has swapped it out since the server started.

    In-process, the server imported CTranslate2 once at its first load and never read the disk
    again. A worker imports it afresh every time it starts — including the restart after a fault,
    which may be hours after someone ran ``uv run pytest`` and replaced the GPU build with the
    CPU one (D-064). Without this, recovering from a GPU fault would quietly land on the CPU.
    """
    from .acceleration import repair_kept_wheel

    try:
        message = repair_kept_wheel()
    except Exception:  # noqa: BLE001 - a failed repair leaves the CPU build, which still works
        logger.warning("Could not check the GPU build of CTranslate2", exc_info=True)
        return
    if message:
        logger.info(message)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
