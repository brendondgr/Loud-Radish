"""The frame clock: what the instrument is doing, and how often to redraw it (Plan 5).

Its input is **(capture mode, run state)**, never run state alone — see `visual_states` for why.

There is no `prefers-reduced-motion` in a system tray and no way to ask for one, so the honest
substitute is a setting: `hold_still` renders each state's resting amplitude and stops advancing.
The instrument still changes when the state does; it simply stops moving within a state.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from .aperture import Frame, render
from .visual_states import SERVER_DOWN, VISUALS, VisualState, visual_for

#: Redraw rate. Ten a second is smooth enough for a 22-pixel icon and cheap enough to leave the CPU
#: to the speech model, which is the thing actually doing work.
DEFAULT_FPS: Final = 10


@dataclass(frozen=True)
class Snapshot:
    """What the application is doing, as the companion last saw it."""

    reachable: bool = False
    mode: str = "live"
    state: str = "idle"
    running_pass: bool = False
    #: What the dictation service is doing, or empty when it is idle. Carried separately from
    #: ``state`` because a dictation is not a capture session (D-049) — it has its own lifecycle
    #: and the two cannot run at once.
    dictation: str = ""

    def visual(self) -> VisualState:
        if not self.reachable:
            return VISUALS[SERVER_DOWN]
        return visual_for(
            self.mode, self.state, running_pass=self.running_pass, dictation=self.dictation
        )


class FrameClock:
    """Advances the animation and calls back with each frame."""

    def __init__(
        self,
        on_frame: Callable[[Frame, VisualState], None],
        *,
        fps: int = DEFAULT_FPS,
        size: int = 22,
        hold_still: bool = False,
    ) -> None:
        self._on_frame = on_frame
        self._interval = 1.0 / max(1, fps)
        self._size = size
        self._hold_still = hold_still

        self._snapshot = Snapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = time.monotonic()
        self._last_visual: str = ""

    @property
    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def update(self, snapshot: Snapshot) -> bool:
        """Adopt new application state. Returns whether the *picture* changed."""
        with self._lock:
            previous = self._snapshot.visual().name
            self._snapshot = snapshot
            current = snapshot.visual().name
        return previous != current

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="aperture-clock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def draw_once(self) -> Frame:
        """Render the current state right now, without the loop."""
        visual = self.snapshot.visual()
        # Time is measured from when the *picture* last changed, not from process start, so every
        # state begins its cycle at the beginning rather than partway through.
        t = 0.0 if self._hold_still else time.monotonic() - self._started
        frame = render(visual, t, size=self._size)
        self._on_frame(frame, visual)
        return frame

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            visual = self.snapshot.visual()
            if visual.name != self._last_visual:
                self._last_visual = visual.name
                self._started = time.monotonic()
            if self._hold_still and visual.name == self._last_visual:
                # Held: the picture still changes with the state, it just stops moving within one.
                continue
            try:
                self.draw_once()
            except Exception:  # noqa: BLE001 - a bad frame must not end the animation
                continue
