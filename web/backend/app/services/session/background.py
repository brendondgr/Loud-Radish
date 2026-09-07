"""The workers a session runs beside itself, and the engine it runs on.

Split out of ``manager.py`` when that file passed twice its 800-line cap. **Mixin methods on**
:class:`~.manager.SessionManager` — see the note in ``frames.py`` for why that shape was chosen.

Three things live here, and none of them is on the path a frame takes: the status ticker that
publishes pipeline health, the context worker that writes rolling summaries and the glossary, and
the polish worker that rewrites finished minutes (D-018). Each is startable and stoppable
independently, because each can be absent on a machine that cannot run it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ...config import AppConfig
from ..streaming.passthrough import build_engine
from . import degradation
from .shapes import STATUS_INTERVAL_S

logger = logging.getLogger(__name__)


class BackgroundWorkMixin:
    """See the module docstring: these are methods of ``SessionManager``."""

    # -- status ticker -------------------------------------------------------------

    def _start_status_thread(self) -> None:
        self._status_stop.clear()
        self._status_thread = threading.Thread(
            target=self._status_loop, name="status-ticker", daemon=True
        )
        self._status_thread.start()

    def _status_loop(self) -> None:
        while not self._status_stop.wait(STATUS_INTERVAL_S):
            metrics = self.metrics()
            self._emit("status", metrics.as_event())

            # In `recorded` mode this is the *only* sign the application is doing anything: no
            # transcript is being produced, so a figure that climbs is what distinguishes recording
            # from having silently stopped (D-021).
            sink = self._sink
            if sink is not None and not sink.is_closed:
                self._emit(
                    "recording.progress",
                    {"duration_s": round(sink.duration_s, 2), "bytes": sink.bytes_written},
                )

            # **A heartbeat, not just an announcement.** `capture.state` used to be emitted exactly
            # once, when capture started — a fact broadcast into a lossy channel with no
            # reconciliation. Four ordinary events lost it permanently: a page loaded after the
            # emit, a socket reconnect, a second tab, or the emit racing the recorder into
            # existence. In every one of those the monitor pane never learned there was anything to
            # show, and nothing ever corrected it. Re-sent every second, a missed emit costs a
            # second instead of the whole recording.
            if self._recorder is not None:
                self._emit("capture.state", self.capture_state())

            # An application creates playback nodes as media starts, so the set the tap was
            # built from goes stale within seconds of pressing record.
            self._relink_tap()

            # Only warn once the model has actually run: a factor of zero before the speaker starts
            # is not the system falling behind.
            if metrics.engine.inference_passes > 3 and 0.0 < metrics.real_time_factor < 1.0:
                self._emit_failure(
                    degradation.falling_behind(
                        metrics.real_time_factor, self._config.resolve().asr.model
                    )
                )

    # -- construction --------------------------------------------------------------

    def _start_context_worker(self, config: AppConfig) -> None:
        """Start rolling summarisation, if anything is configured to do it.

        Wrapped: a failure to build the worker must not prevent a session from starting. The
        assistant is a tool, the transcript is the document.
        """
        if self.context_worker_factory is None or self._store is None:
            return
        try:
            self._context_worker = self.context_worker_factory(self._store, config)
            self._context_worker.start()
        except Exception:  # noqa: BLE001 - never fatal to a recording
            logger.warning("Rolling summaries are unavailable this session", exc_info=True)
            self._context_worker = None

    async def _stop_context_worker(self) -> None:
        worker, self._context_worker = self._context_worker, None
        if worker is None:
            return
        try:
            await worker.stop()
        except Exception:  # noqa: BLE001 - a failed final summary must not fail the stop
            logger.warning("Final summary failed", exc_info=True)

    def _start_polish_worker(self) -> None:
        """Start the minute-by-minute polish pass, if anything is configured to do it.

        Wrapped for the same reason as the context worker: the transcript is the document and the
        polish is a reading aid, so a failure to build one must not stop a recording.
        """
        if self.polish_worker_factory is None or self._store is None:
            return
        try:
            self._polish_worker = self.polish_worker_factory(self._store)
            self._polish_worker.start()
        except Exception:  # noqa: BLE001 - never fatal to a recording
            logger.warning("The transcript polish pass is unavailable this session", exc_info=True)
            self._polish_worker = None

    async def _stop_polish_worker(self) -> None:
        worker, self._polish_worker = self._polish_worker, None
        if worker is None:
            return
        try:
            await worker.stop()
        except Exception:  # noqa: BLE001 - a failed final pass must not fail the stop
            logger.warning("The final polish pass failed", exc_info=True)

    def _build_engine(self, config: AppConfig) -> Any:
        """The streaming engine, which is what makes a session transcribe as it goes."""
        assert self._store is not None
        return build_engine(
            config=config.streaming,
            transcribe=self._asr.transcribe,
            capabilities=self._asr.backend.capabilities if self._asr.backend else None,  # type: ignore[arg-type]
            model_id=self._asr.model_id,
            prompt_builder=self._prompts,
            first_segment_id=self._store.last_segment_id() + 1,
        )
