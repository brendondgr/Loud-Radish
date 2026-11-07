"""Holding a session and letting it go again (D-044).

Split out of ``manager.py`` when the record sheet (D-075) brought that file to its 800-line cap.
**Mixin methods on** :class:`~.manager.SessionManager` — see the note in ``frames.py`` for why that
shape was chosen. Pause and resume are one unit: a flag the capture thread reads, the window
recorder held alongside it, and the two events that tell the interface.
"""

from __future__ import annotations

import logging

from .shapes import SessionError

logger = logging.getLogger(__name__)


class HoldMixin:
    """See the module docstring: these are methods of ``SessionManager``."""

    @property
    def is_paused(self) -> bool:
        """Whether a running session is being held. False when nothing is running."""
        return self._paused and self.is_running

    def pause(self) -> None:
        """Hold the capture. The recording stops growing and the clock stops with it.

        **Pause removes time from the recording rather than recording silence (D-044).** Frames are
        dropped at `_on_frame`, so the WAV stops growing, the engine consumes nothing and its
        `session_seconds` — which is where every transcript timestamp comes from — stops advancing.
        A ten-minute talk held for five minutes produces ten minutes of audio whose last timestamp
        is 10:00.

        The alternative, writing silence, was rejected twice over: it makes the transcript claim
        time in which nothing was said, and it makes pausing cost exactly as much disk and exactly
        as much inference as not pausing, which is not what the word means. What is given up is that
        timestamps no longer correspond to time of day once a session has been paused — and nothing
        reads them that way. The polish pass, the assistant's citations and the exported page's
        video sync are all recording-relative.

        The device stays open. Closing and reopening it would change the stream, the clock origin
        and possibly the sink, and in `window` mode would ask the portal again.

        Raises:
            SessionError: if nothing is recording.
        """
        if not self.is_running:
            raise SessionError("No session is recording.")
        if self._paused:
            return

        self._paused = True
        self._pause_window_capture()
        self._emit(
            "session.paused",
            {
                "session_id": self._metadata.session_id if self._metadata else "",
                "at_seconds": round(self.session_seconds, 2),
            },
        )
        logger.info("Session held at %.1f s", self.session_seconds)

    def resume(self) -> None:
        """Continue a held capture, in the same session, file and store.

        Raises:
            SessionError: if nothing is recording.
        """
        if not self.is_running:
            raise SessionError("No session is recording.")
        if not self._paused:
            return

        self._paused = False
        self._resume_window_capture_after_pause()
        self._emit(
            "session.resumed",
            {
                "session_id": self._metadata.session_id if self._metadata else "",
                "at_seconds": round(self.session_seconds, 2),
            },
        )
        logger.info("Session resumed at %.1f s", self.session_seconds)
