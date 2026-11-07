"""Several audio inputs, heard as one source (D-081).

A window's sound and the microphone together, each at its own volume and each mutable while
recording. The session sees one :class:`~.base.AudioSource`, exactly as it always has, so nothing
downstream — the ring buffer, the VAD, the speech model, the WAV sink, pause — learns that more than
one input exists.

**The hard part is time, not arithmetic.** A microphone (PortAudio) and a tap on the machine's
output (``pw-record``) run on two independent clocks, and two clocks drift. So the mixer keeps **no
clock of its own**: one input is the *primary*, every frame it delivers produces exactly one mixed
frame, and every other input is read from a short queue at the same pace.

* An input that falls behind is padded with silence for the shortfall — never waited for, because a
  mix that stalls on its slowest input is a recording that stops when Bluetooth hiccups.
* An input that runs ahead has its oldest samples dropped once its queue passes
  :data:`MAX_LAG_MS`, which bounds how far it can ever drift from the primary.
* Following the primary rather than wall-clock time keeps a file source's faster-than-real-time
  playback working: the mix runs exactly as fast as the file is read.

The primary is chosen by what keeps the most reliable time: a file, then the microphone (a hardware
clock that never pauses), then anything else. If the primary falls silent — delivers no frames — for
:data:`PRIMARY_QUIET_S`, or fails, the next input is promoted.

**Losing one input does not end the recording.** A microphone unplugged mid-talk leaves the
window's sound recording, and the caller is told through ``on_input_lost`` so the user is too. Only
when every input has gone does the source end.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..formats import DTYPE, SAMPLE_RATE
from ..mixer import InputLevel, apply_gain, mix, rms
from .base import AudioSource, ErrorCallback, FrameCallback, SourceInfo

logger = logging.getLogger(__name__)

#: How far a secondary input may run ahead of the primary before its oldest audio is dropped.
#: Enough to absorb scheduling jitter between two capture threads; short enough that narration
#: never drifts audibly out of step with what it narrates.
MAX_LAG_MS = 200

#: How long the primary may deliver nothing before another input takes over the clock.
PRIMARY_QUIET_S = 0.5

#: How often the watchdog looks at the primary.
WATCH_INTERVAL_S = 0.1

#: Which kinds of input keep the best time, best first. Anything unlisted ranks after these.
CLOCK_PREFERENCE = ("file", "microphone")

#: Called with an input's name and its post-gain frame, once per mixed frame — what that input
#: contributed. How each input's own track is written (Phase 3).
TrackCallback = Callable[[str, np.ndarray], None]

#: Called when one input stops: its name, and the error (``None`` for a clean end).
InputLostCallback = Callable[[str, Exception | None], None]


@dataclass(frozen=True)
class MixInput:
    """One named input to a mix."""

    #: What the API and the interface call it: ``window``, ``system``, ``microphone``.
    name: str
    source: AudioSource
    #: What sort of clock it has, for choosing the primary: ``file``, ``microphone``, ``tap``.
    kind: str = ""


@dataclass
class _Lane:
    """One input's running state inside the mixer."""

    input: MixInput
    level: InputLevel = field(default_factory=InputLevel)
    #: The gain the last frame ended on, so the next one ramps from there.
    gain: float = 1.0
    queue: deque[np.ndarray] = field(default_factory=deque)
    queued: int = 0
    alive: bool = True
    last_frame_at: float = 0.0
    #: The input's own level before gain, for a meter that still moves while it is turned down.
    level_rms: float = 0.0


class MixedSource(AudioSource):
    """Mixes named inputs, each with its own volume and mute, into one stream of frames."""

    def __init__(
        self,
        inputs: list[MixInput],
        *,
        frame_ms: int = 32,
        levels: dict[str, InputLevel] | None = None,
        on_input_lost: InputLostCallback | None = None,
        on_track: TrackCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
        watch: bool = True,
    ) -> None:
        super().__init__(frame_ms=frame_ms)
        if not inputs:
            raise ValueError("A mix needs at least one input.")
        names = [entry.name for entry in inputs]
        if len(set(names)) != len(names):
            raise ValueError(f"Mix inputs must have distinct names: {names}")

        levels = levels or {}
        self._lanes: dict[str, _Lane] = {}
        for entry in inputs:
            level = levels.get(entry.name, InputLevel())
            self._lanes[entry.name] = _Lane(input=entry, level=level, gain=level.gain)
        self._order = names
        self._primary = self._choose_primary()
        self._on_input_lost = on_input_lost
        self._on_track = on_track
        self._clock = clock
        self._watch = watch
        self._max_lag = int(SAMPLE_RATE * MAX_LAG_MS / 1000)
        self._lock = threading.Lock()
        self._stop_watch = threading.Event()
        self._watcher: threading.Thread | None = None
        #: Samples dropped from secondary inputs for running ahead. For diagnostics and tests.
        self.dropped_samples = 0
        #: Samples of silence added to secondary inputs for falling behind.
        self.padded_samples = 0

    # -- what it is --------------------------------------------------------------------

    @property
    def names(self) -> list[str]:
        """Input names, in the order they were given."""
        return list(self._order)

    @property
    def primary(self) -> str:
        """The input whose frames currently drive the mix."""
        return self._primary

    @property
    def info(self) -> SourceInfo:
        """One input's own description when there is one, so a single-input run reads as before."""
        if len(self._lanes) == 1:
            return next(iter(self._lanes.values())).input.source.info
        parts = [self._lanes[name].input.source.info for name in self._order]
        return SourceInfo(
            id="mix:" + "+".join(self._order),
            name=" + ".join(part.name for part in parts),
            kind="mix",
            sample_rate=SAMPLE_RATE,
            frame_ms=self._frame_ms,
        )

    # -- levels ------------------------------------------------------------------------

    def set_level(
        self, name: str, *, volume: int | None = None, muted: bool | None = None
    ) -> InputLevel:
        """Change one input's volume or mute. Takes effect on the next frame, ramped.

        Raises:
            KeyError: for an input this mix does not have.
        """
        with self._lock:
            lane = self._lanes[name]
            current = lane.level
            lane.level = InputLevel(
                volume=current.volume if volume is None else int(volume),
                muted=current.muted if muted is None else bool(muted),
            )
            return lane.level

    def levels(self) -> dict[str, dict[str, object]]:
        """Each input's setting and its current level, for the interface's meters."""
        with self._lock:
            return {
                name: {
                    **lane.level.as_dict(),
                    "level": round(lane.level_rms, 4),
                    "alive": lane.alive,
                }
                for name, lane in ((n, self._lanes[n]) for n in self._order)
            }

    # -- lifecycle ---------------------------------------------------------------------

    def start(self, on_frame: FrameCallback, on_error: ErrorCallback | None = None) -> None:
        """Start every input. If one will not start, the ones already started are stopped."""
        if self.is_running:
            raise RuntimeError("Source is already running")
        self._on_frame = on_frame
        self._on_error = on_error
        self._running.set()
        started: list[AudioSource] = []
        now = self._clock()
        try:
            for name in self._order:
                lane = self._lanes[name]
                lane.last_frame_at = now
                lane.input.source.start(
                    lambda frame, n=name: self._on_input_frame(n, frame),
                    lambda error, n=name: self._on_input_error(n, error),
                )
                started.append(lane.input.source)
        except Exception:
            self._running.clear()
            for source in started:
                source.stop()
            raise

        if self._watch and len(self._lanes) > 1:
            self._stop_watch.clear()
            self._watcher = threading.Thread(target=self._watch_loop, name="mix-watch", daemon=True)
            self._watcher.start()

    def stop(self) -> None:
        """Stop every input."""
        self._running.clear()
        self._stop_watch.set()
        for name in self._order:
            try:
                self._lanes[name].input.source.stop()
            except Exception:  # noqa: BLE001 - one input failing to stop must not strand the rest
                logger.exception("Mix input %s did not stop cleanly", name)
        watcher = self._watcher
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(timeout=1.0)
        self._watcher = None

    # -- the mix -----------------------------------------------------------------------

    def _on_input_frame(self, name: str, frame: np.ndarray) -> None:
        """One frame from one input. Only the primary's frames produce output."""
        with self._lock:
            lane = self._lanes[name]
            if not lane.alive:
                return
            lane.last_frame_at = self._clock()
            lane.level_rms = rms(frame)
            if name != self._primary:
                self._enqueue(lane, frame)
                return
            mixed, contributions = self._mix_with(lane, frame)

        if self._on_track is not None:
            for track, contribution in contributions:
                try:
                    self._on_track(track, contribution)
                except Exception:  # noqa: BLE001 - a track write must not stop the mix
                    logger.exception("Writing the %s track failed", track)
        self._emit(mixed)

    def _mix_with(
        self, primary: _Lane, frame: np.ndarray
    ) -> tuple[np.ndarray, list[tuple[str, np.ndarray]]]:
        """The primary's frame plus the same span of every other live input. Holds the lock."""
        size = frame.size
        contributions: list[tuple[str, np.ndarray]] = []
        for name in self._order:
            lane = self._lanes[name]
            if not lane.alive:
                continue
            raw = frame if lane is primary else self._take(lane, size)
            target = lane.level.gain
            contributions.append((name, apply_gain(raw, lane.gain, target)))
            lane.gain = target
        return mix([part for _, part in contributions]), contributions

    def _enqueue(self, lane: _Lane, frame: np.ndarray) -> None:
        """Queue a secondary input's frame, dropping its oldest audio past the lag bound."""
        lane.queue.append(frame)
        lane.queued += frame.size
        while lane.queued > self._max_lag and lane.queue:
            excess = lane.queued - self._max_lag
            head = lane.queue[0]
            if head.size <= excess:
                lane.queue.popleft()
                lane.queued -= head.size
                self.dropped_samples += head.size
            else:
                lane.queue[0] = head[excess:]
                lane.queued -= excess
                self.dropped_samples += excess

    def _take(self, lane: _Lane, size: int) -> np.ndarray:
        """``size`` samples from a secondary's queue, padded with silence if it has fewer."""
        parts: list[np.ndarray] = []
        needed = size
        while needed and lane.queue:
            head = lane.queue[0]
            if head.size <= needed:
                parts.append(lane.queue.popleft())
                needed -= head.size
            else:
                parts.append(head[:needed])
                lane.queue[0] = head[needed:]
                needed = 0
        lane.queued -= size - needed
        if needed:
            parts.append(np.zeros(needed, dtype=DTYPE))
            self.padded_samples += needed
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    # -- the primary -------------------------------------------------------------------

    def _choose_primary(self, exclude: str = "") -> str:
        """The live input with the most reliable clock, or ``""`` when none is left."""
        candidates = [
            name
            for name in getattr(self, "_order", [])
            if name != exclude and self._lanes[name].alive
        ]
        if not candidates:
            return ""

        def rank(name: str) -> int:
            kind = self._lanes[name].input.kind
            return (
                CLOCK_PREFERENCE.index(kind) if kind in CLOCK_PREFERENCE else len(CLOCK_PREFERENCE)
            )

        return min(candidates, key=lambda name: (rank(name), self._order.index(name)))

    def check_primary(self) -> str:
        """Promote another input if the primary has gone quiet. Returns the primary afterwards.

        Run by the watchdog thread; public so a test can drive it with a controlled clock.
        """
        with self._lock:
            current = self._lanes.get(self._primary)
            if current is None or len(self._lanes) == 1:
                return self._primary
            now = self._clock()
            if now - current.last_frame_at < PRIMARY_QUIET_S:
                return self._primary
            # Only to an input that is itself delivering: promoting one that is equally quiet
            # changes nothing but the log.
            fresh = [
                name
                for name in self._order
                if name != self._primary
                and self._lanes[name].alive
                and now - self._lanes[name].last_frame_at < PRIMARY_QUIET_S
            ]
            if not fresh:
                return self._primary
            previous, self._primary = self._primary, fresh[0]
            # The promoted input's queue is the audio it delivered while it was secondary; it is
            # already behind the clock it now sets, so it is discarded rather than replayed.
            promoted = self._lanes[self._primary]
            promoted.queue.clear()
            promoted.queued = 0
        logger.warning(
            "Mix input %s delivered nothing for %.1fs; %s now keeps the time.",
            previous,
            PRIMARY_QUIET_S,
            self._primary,
        )
        return self._primary

    def _watch_loop(self) -> None:
        while not self._stop_watch.wait(WATCH_INTERVAL_S):
            try:
                self.check_primary()
            except Exception:  # noqa: BLE001 - the watchdog must outlive one bad check
                logger.exception("Mix watchdog check failed")

    # -- losing an input ---------------------------------------------------------------

    def _on_input_error(self, name: str, error: Exception | None) -> None:
        """One input stopped. The mix goes on without it unless it was the last, or a file."""
        with self._lock:
            lane = self._lanes[name]
            if not lane.alive:
                return
            lane.alive = False
            lane.queue.clear()
            lane.queued = 0
            # A file reaching its end is the recording reaching its end: the file *is* the talk
            # being replayed, and the mix keeps its time.
            ends_mix = error is None and lane.input.kind == "file" and name == self._primary
            if name == self._primary:
                self._primary = self._choose_primary(exclude=name)
            remaining = self._primary
        if not self.is_running:
            return
        if ends_mix or not remaining:
            self._fail(error)
            return
        logger.warning("Mix input %s stopped (%s); recording continues without it.", name, error)
        if self._on_input_lost is not None:
            try:
                self._on_input_lost(name, error)
            except Exception:  # noqa: BLE001 - a notification must not stop the mix
                logger.exception("Input-lost notification failed")
