"""Model loading, warm-up, swapping, and unloading (BE §6.6).

Four things that all have to be right, and each of which is visible to the user when it is not:

* **Load** may take 5–30 seconds and significant memory, so it runs off the event loop with progress
  reported to the UI. A frozen window during model load reads as a crash.
* **Warm-up** runs one inference on silence immediately after loading. The first real inference is
  otherwise several times slower than steady state, which corrupts latency measurements and makes
  the first seconds of a talk feel broken.
* **Swap** must flush the buffer, commit any pending hypothesis, and mark the switch point, so the
  transcript records which model produced which text.
* **Unload** frees device memory explicitly, which matters when a local LLM shares the same GPU.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from ...config.schema import AsrConfig
from ..audio.formats import SAMPLE_RATE
from .contract import AsrBackend, AsrLoadError, AsrResult
from .registry import build_backend

logger = logging.getLogger(__name__)

#: Duration of the silence buffer used for warm-up.
WARM_UP_SECONDS = 1.0


class LoadState(StrEnum):
    """Where the model is in its lifecycle, as reported to the UI."""

    UNLOADED = "unloaded"
    LOADING = "loading"
    WARMING = "warming"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class LoadProgress:
    """One progress report during loading.

    The UI shows a named progress state — "Loading Whisper small — warming up" — rather than an
    unlabelled spinner, because a user who cannot start recording deserves to know why.
    """

    state: LoadState
    model_id: str
    message: str
    #: ``None`` when genuinely unknown, rather than a fabricated percentage.
    fraction: float | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-safe payload for the progress event."""
        return {
            "state": str(self.state),
            "model_id": self.model_id,
            "message": self.message,
            "fraction": self.fraction,
        }


ProgressCallback = Callable[[LoadProgress], None | Awaitable[None]]


class AsrLifecycle:
    """Owns the loaded backend and the transitions between models."""

    def __init__(self, config: AsrConfig, on_progress: ProgressCallback | None = None) -> None:
        self._config = config
        self._on_progress = on_progress
        self._backend: AsrBackend | None = None
        self._state = LoadState.UNLOADED
        self._error: str = ""
        self._lock = asyncio.Lock()

    # -- state ---------------------------------------------------------------------

    @property
    def state(self) -> LoadState:
        """The current lifecycle state."""
        return self._state

    @property
    def backend(self) -> AsrBackend | None:
        """The loaded backend, or ``None``."""
        return self._backend

    @property
    def is_ready(self) -> bool:
        """Whether transcription can proceed."""
        return self._state is LoadState.READY and self._backend is not None

    @property
    def model_id(self) -> str:
        """The loaded model's identifier, or an empty string."""
        return self._backend.model_id if self._backend else ""

    @property
    def error(self) -> str:
        """The last load failure message, or an empty string."""
        return self._error

    def status(self) -> dict[str, object]:
        """A JSON-safe summary for the status event and the settings UI."""
        return {
            "state": str(self._state),
            "model_id": self.model_id,
            "error": self._error,
            "capabilities": (self._backend.capabilities.as_dict() if self._backend else None),
        }

    # -- loading -------------------------------------------------------------------

    async def load(self, config: AsrConfig | None = None) -> None:
        """Load and warm up the configured backend.

        The blocking work runs in a worker thread so the event loop keeps serving the WebSocket —
        the UI must stay responsive precisely while it is showing load progress.
        """
        async with self._lock:
            if config is not None:
                self._config = config
            await self._unload_locked()

            backend = build_backend(self._config)
            await self._report(LoadState.LOADING, backend.model_id, "Loading model")

            try:
                await asyncio.to_thread(backend.load)
            except AsrLoadError as exc:
                self._state = LoadState.FAILED
                self._error = str(exc)
                await self._report(LoadState.FAILED, backend.model_id, str(exc))
                raise

            await self._report(LoadState.WARMING, backend.model_id, "Warming up")
            await asyncio.to_thread(self._warm_up, backend)

            self._backend = backend
            self._state = LoadState.READY
            self._error = ""
            await self._report(LoadState.READY, backend.model_id, "Ready", fraction=1.0)
            logger.info("ASR backend ready: %s", backend.model_id)

    @staticmethod
    def _warm_up(backend: AsrBackend) -> None:
        """Run one inference on silence, tolerating a backend that dislikes empty input."""
        try:
            backend.transcribe(np.zeros(int(WARM_UP_SECONDS * SAMPLE_RATE), dtype=np.float32))
        except Exception:  # noqa: BLE001 - warm-up is an optimisation, never a gate
            logger.debug("Warm-up inference failed; continuing", exc_info=True)

    # -- swapping and unloading ----------------------------------------------------

    async def swap(self, config: AsrConfig) -> None:
        """Change models mid-session.

        The caller is responsible for flushing the audio buffer and force-committing any pending
        hypothesis *before* calling this. Text produced by the old model must not be attributed to
        the new one, which is why every segment carries a ``model_id``.
        """
        previous = self.model_id
        await self.load(config)
        logger.info("Swapped ASR model: %s → %s", previous or "none", self.model_id)

    async def unload(self) -> None:
        """Release the model and its device memory."""
        async with self._lock:
            await self._unload_locked()

    async def _unload_locked(self) -> None:
        """Unload without taking the lock. Caller holds it."""
        if self._backend is None:
            self._state = LoadState.UNLOADED
            return
        model_id = self._backend.model_id
        await asyncio.to_thread(self._backend.unload)
        self._backend = None
        self._state = LoadState.UNLOADED
        await self._report(LoadState.UNLOADED, model_id, "Model unloaded")

    # -- transcription -------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> AsrResult:
        """Transcribe through the loaded backend.

        Called from the ASR worker thread rather than the event loop, so it is deliberately
        synchronous.
        """
        backend = self._backend
        if backend is None or self._state is not LoadState.READY:
            raise AsrLoadError(
                f"No model is loaded (state: {self._state}). "
                + (self._error or "Load a model before transcribing.")
            )
        if not backend.capabilities.accepts_prompt:
            prompt = None
        return backend.transcribe(audio, prompt)

    # -- internals -----------------------------------------------------------------

    async def _report(
        self,
        state: LoadState,
        model_id: str,
        message: str,
        fraction: float | None = None,
    ) -> None:
        """Emit a progress update, tolerating a callback that raises."""
        self._state = state
        if self._on_progress is None:
            return
        progress = LoadProgress(state=state, model_id=model_id, message=message, fraction=fraction)
        try:
            result = self._on_progress(progress)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - a broken listener must not fail the load
            logger.exception("ASR progress listener raised")
