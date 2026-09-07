"""What happens to each captured frame, on the two threads that touch one.

Split out of ``manager.py`` when that file passed twice its 800-line cap. These are **mixin methods
on** :class:`~.manager.SessionManager`, not an object of their own: they read and write the
manager's attributes directly and have no life without it. The split is for navigability and for the
cap, and it is honest about being that rather than claiming an object boundary it does not have.
A genuine collaborator — one that owns its own state and could be tested alone — is the deeper
refactor this leaves available.

Two threads meet here. ``_on_frame`` runs on the capture thread and must never block; everything
from ``_handle_frame`` down runs on the ASR worker thread and may take as long as inference does.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from ...models.segment import Segment
from ..asr.contract import AsrLoadError
from ..streaming.events import CommittedSegment, EngineNotice, HypothesisUpdate
from . import degradation
from .shapes import LEVEL_INTERVAL_S, LOOPBACK_SPEECH_RMS, CapturedFrame

logger = logging.getLogger(__name__)


class FramePathMixin:
    """See the module docstring: these are methods of ``SessionManager``."""

    # -- capture path --------------------------------------------------------------

    def _on_frame(self, frame: np.ndarray) -> None:
        """Called on the capture thread. Must return quickly and must never block."""
        gate = self._gate
        if gate is None:
            return

        # **The pause gate, and the only one (D-044).** This method is where a frame is written to
        # the recording *and* where it is queued for inference, so one flag here stops both in step
        # and nothing downstream needs to know the session can be held. Anywhere else would stop
        # one and not the other, and a WAV that keeps growing while the engine's clock does not is
        # a recording whose timestamps are wrong from the pause onward.
        #
        # The meter keeps running. A paused session with a dead level meter looks like a broken
        # one; a moving meter says "we can still hear you, and we are not writing it down", which
        # is exactly the state.
        if self._paused:
            self._emit_level(frame, False, False)
            return

        result = gate.process(frame)

        # Written here, on the capture thread, rather than through the queue. The queue is
        # drop-oldest by design — it protects inference latency by discarding audio — and audio
        # discarded from a *recording* is a hole in the only copy of the talk. A buffered write of
        # a kilobyte is several orders of magnitude cheaper than the inference pass the queue
        # exists to decouple from, so this does not reintroduce the blocking it guards against.
        sink = self._sink
        if sink is not None and sink.write(frame) and not sink.is_closed:
            self._emit_failure(degradation.recording_capped(sink.duration_s / 60.0))

        # **A monitor source is gated on an absolute level, not an adaptive one**, and getting this
        # wrong in either direction produces a different visible fault.
        #
        # `EnergyVad` compares each frame against an *adaptive noise floor*. That is right for a
        # microphone in a room: speech spikes above a floor that settles into the gaps between
        # phrases. System output has no gaps — the floor rises to meet continuous content and
        # nothing ever clears it. Measured: **61% of frames from microphone speech pass, against 8%
        # from the system's own output**, and with three-frame hysteresis 8% scattered frames never
        # open the gate. Window recordings therefore consumed audio and committed nothing.
        #
        # Bypassing the gate entirely was tried and is worse. Whisper *invents* text on non-speech,
        # and submitting every buffer produced a transcript of disjointed fragments — "my other
        # children", "yeah it happens father" — from a gaming video's background music. A wrong
        # transcript is worse than an empty one, because it is read as real.
        #
        # An absolute threshold separates them, because a digital output has *true* silence where a
        # room only has a noise floor. Measured over one-frame RMS: seminar speech runs from −57 to
        # −21 dBFS, a 35 dB range; a video's background music sat between −42.6 and −37.4, a 5 dB
        # range. Speech has dynamics and music at conversational volume does not.
        speaking = result.speaking or self._loopback_has_content(frame)

        dropped = self._queue.put(
            CapturedFrame(audio=frame, speaking=speaking, pause=result.pause_event)
        )
        if dropped and not self._warned_backpressure:
            self._warned_backpressure = True
            self._emit_failure(
                degradation.dropped_audio(self._queue.dropped, self._config.resolve().asr.model)
            )

        self._emit_level(frame, result.state_changed, result.speaking)

    def _loopback_has_content(self, frame: np.ndarray) -> bool:
        """Whether a monitor frame is loud enough to be worth transcribing.

        Only ever true for a loopback source; a microphone keeps the adaptive gate, which earns its
        place there by stopping inference running on an empty room.
        """
        if not self._source_is_loopback:
            return False
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        return rms >= LOOPBACK_SPEECH_RMS

    @property
    def _source_is_loopback(self) -> bool:
        """Whether the open source is a monitor of the machine's own output.

        Read from the source rather than from configuration, because the file source and the
        application tap both resolve to something the configuration does not name directly.
        """
        info = self._source_info
        return info is not None and info.kind == "loopback"

    def _emit_level(self, frame: np.ndarray, state_changed: bool, speaking: bool) -> None:
        """Publish the level meter, throttled, and the VAD state only when it changes."""
        if state_changed:
            self._emit("vad.state", {"speaking": speaking})

        now = time.monotonic()
        if now - self._last_level_emit < LEVEL_INTERVAL_S:
            return
        self._last_level_emit = now
        self._emit("audio.level", self._meter.update(frame).as_event())

    def _handle_frame(self, frame: CapturedFrame) -> None:
        """Called on the ASR worker thread. This is the expensive one, and it may block."""
        engine = self._engine
        if engine is None:
            return
        try:
            events = engine.add_audio(frame.audio, speaking=frame.speaking, pause=frame.pause)
        except AsrLoadError as exc:
            self._emit_failure(degradation.model_load_failed(self._asr.model_id, str(exc)))
            return
        self._dispatch(events)

    def _dispatch(self, events: list[Any]) -> None:
        """Persist and publish whatever the engine produced."""
        for event in events:
            if isinstance(event, CommittedSegment):
                self._persist(event.segment)
                self._emit("transcript.committed", event.segment.as_event())
            elif isinstance(event, HypothesisUpdate):
                self._emit("transcript.hypothesis", {"text": event.text, "start": event.start})
            elif isinstance(event, EngineNotice):
                self._emit("error", event.as_event()["data"])

    def _persist(self, segment: Segment) -> None:
        """Write a segment through to disk, surviving a write failure.

        A full disk must not end the session: the transcript stays in memory and is still on screen,
        and freeing space resumes saving. Stopping here would guarantee losing what might be saved.
        """
        if self._store is None:
            return
        try:
            self._store.append_segment(segment)
        except Exception as exc:  # noqa: BLE001 - any storage error, not just OSError
            logger.exception("Could not persist segment %s", segment.id)
            self._emit_failure(degradation.disk_full(type(exc).__name__))

    def _on_source_error(self, error: Exception | None) -> None:
        """The audio source stopped. ``None`` means it reached the end of its input."""
        if error is None:
            return
        name = self._source_info.name if self._source_info else "The audio device"
        self._emit_failure(degradation.device_lost(name))
