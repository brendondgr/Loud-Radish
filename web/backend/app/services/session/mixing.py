"""A session's mixer: building it, each input's own track, and telling the user when one is lost.

**Mixin methods on** :class:`~.manager.SessionManager` — see the note in ``frames.py`` for why that
shape was chosen. Kept out of ``manager.py``, which is near its 800-line cap, and out of
``sources.py``, which decides *what* each input is; this decides what happens to them once mixed.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from ...config import AppConfig
from ...models.session import SessionMetadata
from ..audio.mixer import InputLevel
from ..audio.sources import MixedSource, MixInput
from ..recording import SinkError, WavSink, layout_for
from . import degradation

logger = logging.getLogger(__name__)


class MixingMixin:
    """See the module docstring: these are methods of ``SessionManager``."""

    def _build_mix(self, config: AppConfig, inputs: list[MixInput]) -> MixedSource:
        """One source over this run's inputs, at the volumes the record sheet started them at."""
        options = self._options
        levels = {
            name: InputLevel(volume=volume)
            for name, volume in ((options.levels if options else {}) or {}).items()
        }
        mix = MixedSource(
            inputs,
            frame_ms=config.audio.frame_ms,
            levels=levels,
            on_input_lost=self._on_input_lost,
            on_track=self._on_track if len(inputs) > 1 else None,
        )
        self._mix = mix
        return mix

    # -- each input's own track ----------------------------------------------------------

    def _open_input_tracks(self, config: AppConfig, session: SessionMetadata) -> None:
        """A WAV per input beside the mixed one, when there is more than one and audio is kept.

        **Only when `storage.retain_audio` is on.** The tracks are audio, and a user who chose not
        to keep audio must not find their microphone kept on its own; the mixed file follows the
        same rule after its pass. They are also only worth having when there is something to
        separate, which one input never has.

        Opened before the source starts, so their sample zero is the mix's. A track that cannot be
        opened costs that track and nothing else: the mix is the recording.
        """
        self._input_tracks = {}
        options = self._options
        inputs = list(options.audio_inputs) if options else []
        if len(inputs) < 2 or not config.storage.retain_audio or self._sink is None:
            return
        layout = layout_for(config.recording.recording_dir, session.started_at, session.session_id)
        for name in inputs:
            try:
                self._input_tracks[name] = WavSink(
                    layout.input_track(name), max_minutes=config.recording.max_minutes
                )
            except SinkError as exc:
                logger.warning("Could not open the %s track: %s", name, exc)

    def _on_track(self, name: str, frame: np.ndarray) -> None:
        """One input's post-gain frame. Called on the capture thread, once per mixed frame.

        Held by the same pause as the mixed file (D-044), so every track stays the length of the
        recording and lines up with it sample for sample.
        """
        if self._paused:
            return
        sink = self._input_tracks.get(name)
        if sink is not None:
            sink.write(frame)

    def _close_input_tracks(self, *, keep: bool = True) -> None:
        """Finalise each input's track, or discard them all on an abrupt teardown."""
        tracks, self._input_tracks = self._input_tracks, {}
        for name, sink in tracks.items():
            try:
                if keep and sink.samples:
                    sink.close()
                else:
                    sink.discard()
            except SinkError:
                logger.warning("Could not finalise the %s track", name, exc_info=True)

    # -- losing an input -----------------------------------------------------------------

    def _on_input_lost(self, name: str, error: Exception | None) -> None:
        """One input stopped and the rest are still recording. Said once, plainly."""
        self._emit_failure(degradation.audio_input_lost(name, str(error) if error else ""))
        self._emit("mix.changed", self.mix_state() or {})

    # -- what the interface reads --------------------------------------------------------

    def mix_state(self) -> dict[str, Any] | None:
        """The running mix: each input's volume, mute, level and whether it is still delivering."""
        mix = self._mix
        if mix is None or not self.is_running:
            return None
        return {"inputs": mix.names, "primary": mix.primary, "levels": mix.levels()}
