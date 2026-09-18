"""The speech model and a local language model take turns on one GPU (D-072).

Both recordings lost on 2026-09-18 died in the middle of a local language-model generation: gemma-4
on llama.cpp's Vulkan backend and faster-whisper on ROCm, running compute on the same gfx1151 iGPU
at the same moment, and a GPU page fault in the speech model's process. Every window recording
before that day sent the language model to another machine, and none of them faulted.

Whichever layer is really at fault — driver, firmware, or the ROCm CTranslate2 build — the overlap
is what triggers it, and the overlap is optional. This makes it rare:

* **Language-model requests** may run together (a summary and a chat answer), but a request waits
  while a speech pass is running. A pass is short on a GPU, about a second, so the wait is too.
* **A speech pass** waits while any language-model request is running *or waiting* — the waiting
  part, so that a live session passing back to back cannot starve the request forever.
* **The speech pass's wait is bounded** (``asr.gpu_turn_wait_s``). While a pass waits, audio
  queues in the capture queue, which holds about 6.4 s. Waiting longer than that would drop speech
  to prevent a crash that is now survivable (D-071), which is the wrong trade. So past the bound the
  pass runs anyway, alongside the language model, and that is counted.

Turning off reasoning (D-073) is what makes this cheap: a polish request that answers in two or
three seconds fits inside the bound, and a thinking one did not.

The state lives behind a :class:`threading.Condition`, because the two sides live in different
places: speech passes on worker threads, language-model requests on whichever event loop is
streaming them — the server's own, or the one a dictation runs in its thread.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

#: How often a waiting language-model request looks again. It must not hold a thread while it
#: waits, so it polls; 20 ms is nothing next to the second-long pass it is waiting for.
POLL_S = 0.02

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


class GpuTurns:
    """Who is running on the GPU now."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._speech_active = 0
        self._llm_active = 0
        self._llm_waiting = 0
        #: Passes that ran alongside a language model after waiting as long as they may.
        self._overlaps = 0
        #: Said once per stretch of language-model work, not once per pass.
        self._warned = False

    @property
    def overlaps(self) -> int:
        """How many speech passes ran alongside a language model."""
        return self._overlaps

    @property
    def busy(self) -> dict[str, int]:
        """A snapshot, for tests and diagnostics."""
        with self._cond:
            return {
                "speech": self._speech_active,
                "llm": self._llm_active,
                "llm_waiting": self._llm_waiting,
            }

    @contextmanager
    def speech_pass(self, max_wait_s: float) -> Iterator[bool]:
        """Hold the GPU for one speech pass. Yields whether it had the GPU to itself."""
        deadline = time.monotonic() + max_wait_s
        started = time.monotonic()
        with self._cond:
            while self._llm_active or self._llm_waiting:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            alone = not self._llm_active
            if not alone:
                self._overlaps += 1
                if not self._warned:
                    self._warned = True
                    logger.info(
                        "A speech pass waited %.1f s for the language model and is running "
                        "alongside it, so no audio is dropped",
                        time.monotonic() - started,
                    )
            self._speech_active += 1
        try:
            yield alone
        finally:
            with self._cond:
                self._speech_active -= 1
                self._cond.notify_all()

    @asynccontextmanager
    async def language_model(self) -> AsyncIterator[None]:
        """Hold the GPU for one language-model request, waiting out any speech pass."""
        with self._cond:
            self._llm_waiting += 1
        try:
            while True:
                with self._cond:
                    if not self._speech_active:
                        self._llm_waiting -= 1
                        self._llm_active += 1
                        break
                await asyncio.sleep(POLL_S)
        except BaseException:
            # Cancelled while waiting: a waiter that never leaves would hold every speech pass
            # at its bound for the rest of the process.
            with self._cond:
                self._llm_waiting -= 1
                self._cond.notify_all()
            raise
        try:
            yield
        finally:
            with self._cond:
                self._llm_active -= 1
                if not self._llm_active:
                    self._warned = False
                self._cond.notify_all()


def is_loopback(endpoint: str) -> bool:
    """Whether ``endpoint`` is this machine — and so, for a local model, probably this GPU.

    Narrower than the privacy check in the language-model client, which also counts ``.local``
    names: a server elsewhere on the network keeps data private but does not share this GPU.
    """
    try:
        host = (urlsplit(endpoint).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOOPBACK or host.startswith("127.")


_SHARED = GpuTurns()


def shared_turns() -> GpuTurns:
    """The one instance for this process. The GPU is one device, however many callers there are."""
    return _SHARED
