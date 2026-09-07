"""Starting a transcription pass over a finished recording, and what happens to the store (D-021).

Split out of ``manager.py`` when that file passed twice its 800-line cap. **Mixin methods on**
:class:`~.manager.SessionManager` — see the note in ``frames.py`` for why that shape was chosen.

Also home to the retention rules from D-031: a finished session's store stays open for reading until
the next session starts, so the assistant can still be asked about the talk that just ended.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...config import AppConfig
from ...models.session import SessionMetadata
from ..recording import SinkError, TranscriptionJob, TranscriptionRunner
from ..transcript import TranscriptStore, archive
from . import degradation

logger = logging.getLogger(__name__)


class TranscriptionPassMixin:
    """See the module docstring: these are methods of ``SessionManager``."""

    def _start_transcription(self, session: SessionMetadata) -> bool:
        """Begin the post-capture pass, if this session produced a recording.

        Returns whether the store was handed to the runner, which then owns closing it.
        """
        sink, self._sink = self._sink, None
        if sink is None or self._store is None:
            return False

        try:
            path = sink.close()
        except SinkError as exc:
            logger.error("Could not finalise the recording: %s", exc)
            self._emit_failure(degradation.disk_full(str(exc)))
            return False

        if sink.samples == 0:
            # A session that captured nothing has nothing to transcribe, and an empty file left on
            # disk is only ever confusing.
            path.unlink(missing_ok=True)
            logger.info("Session %s captured no audio; nothing to transcribe.", session.session_id)
            return False

        config = self._config.resolve()
        job = TranscriptionJob(
            session_id=session.session_id,
            source_path=str(path),
            total_seconds=sink.duration_s,
        )
        # A session that also transcribed live already holds revision 0, so the post-capture pass
        # writes revision 1 and both are kept (D-022). A `recorded` session has no live pass, so
        # its only transcript is revision 0 and a switch would have nothing to switch between.
        revision = 1 if self._engine is not None else 0

        self._runner = TranscriptionRunner(
            registry=self.jobs,
            emit=self._emit,
            transcribe=self._asr.transcribe,
            window_s=config.recording.batch_window_s,
            overlap_s=config.recording.batch_overlap_s,
            max_segment_s=config.streaming.max_segment_s,
            revision=revision,
            on_released=self._on_transcription_released,
        )
        started = self._runner.start(
            job=job,
            store=self._store,
            # **Retention is not a setting when the video came up short.** The combined file is
            # then the only other copy of the sound and it does not hold all of it, so deleting
            # the WAV would destroy the part no other file contains.
            retain_audio=config.storage.retain_audio or bool(self._audio_shortfall_s),
            prompt=self._prompts.build() if self._prompts else None,
        )
        # The reference is deliberately *kept*, unlike ownership. `GET /api/transcript/...` serves
        # from it, and a reload during a half-hour pass must still show the segments already
        # committed rather than an empty page. The runner alone closes it, and tells us when.
        return started

    def _on_transcription_released(self) -> None:
        """The pass has closed the store. Reopen it for reading, and reclaim ownership.

        **This is the path `recorded` and `window` sessions take, and the fault was reported
        against one of them.** Those modes hand the store to `TranscriptionRunner`, which closes it
        itself when the batch pass finishes — so retaining it in `_teardown` cannot help here, and
        without this the assistant would go back to answering "there is no transcript to ask about
        yet" at precisely the moment the transcript becomes *complete* and most worth asking about.

        Reopened from the path rather than kept, because the object the runner closed is spent.
        `TranscriptStore(path)` opens an existing database — the same call `routes/sessions.py`
        makes for any past session — so this costs one connection and no special case.
        """
        store, self._store = self._store, None
        self._store_handed_over = False
        if store is None:
            return
        try:
            self._retain(TranscriptStore(store.path))
        except Exception:  # noqa: BLE001 - a session that has already ended must still end cleanly
            logger.debug("Could not reopen %s for reading", store.path.name, exc_info=True)

    def reopen_last_session(self) -> bool:
        """Hold the most recent finished transcript open for reading. Returns whether one was.

        **D-031 across a process boundary (D-041).** That decision keeps a finished session's store
        open until the next one starts, so the page and the assistant can still read the talk that
        just ended. It holds the store in an attribute, though, so a restart loses it: the database
        is still on disk and nothing reopens it, and the main page comes up empty while Recordings
        shows the talk perfectly well.

        Called once from the application's lifespan, never during a session. It reads through
        ``archive`` rather than scanning the directory itself, so there is one definition of what
        the sessions on disk are and which of them is newest.

        **Never fatal.** A session directory that cannot be read, or a database that cannot be
        opened, costs the convenience and nothing else — an application that refuses to start
        because of an old file is worse than one that starts with an empty page.
        """
        if self._store is not None or self._last_store is not None:
            return False

        config = self._config.resolve()
        if not config.storage.reopen_last_session:
            return False

        try:
            path = archive.newest_with_segments(config)
        except Exception:
            logger.debug("Could not look for a session to reopen", exc_info=True)
            return False
        if path is None:
            return False

        try:
            self._retain(TranscriptStore(path))
        except Exception:
            logger.info("Could not reopen %s for reading", path.name, exc_info=True)
            return False

        logger.info("Reopened %s, so the last transcript is still readable", path.name)
        return True

    def _retain(self, store: TranscriptStore) -> None:
        """Hold a finished session's store open for reading, replacing any already held."""
        if store is self._last_store:
            return
        self._release_retained()
        self._last_store = store
        logger.debug("Holding %s open for reading", store.path.name)

    def _release_retained(self) -> None:
        """Close the retained store, if there is one. Safe to call more than once."""
        store, self._last_store = self._last_store, None
        if store is None:
            return
        try:
            store.close()
        except Exception:  # noqa: BLE001 - a store we are done with must not break a new session
            logger.debug("The retained transcript store did not close cleanly", exc_info=True)

    def _open_store(self, config: AppConfig, session: SessionMetadata) -> TranscriptStore:
        directory = self._session_dir or Path(config.storage.session_dir)
        path = directory / f"{session.started_at.strftime('%Y%m%d-%H%M%S')}-{session.session_id}.db"
        return TranscriptStore(path, metadata=session)
