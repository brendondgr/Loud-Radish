"""Run a transcription pass in the background and publish what it produces (D-021).

Lives here rather than in the session manager for one reason: the pass **outlives the session**. The
manager's job ends when capture stops and everything it owns is torn down; this keeps a store open,
a thread running, and a job's progress readable for however long the pass takes — which for a
forty-minute talk is around half an hour.

That is also why the transcript store is handed over rather than borrowed. Whoever starts the runner
gives up ownership of the store, and the runner closes it when the pass ends however it ends. Two
owners of one SQLite connection, one of them tearing down while the other writes, is the failure
this arrangement exists to make impossible.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...models.segment import Segment
from ..transcript import TranscriptStore
from .batch import BatchError, transcribe_file
from .job import JobRegistry, TranscriptionJob

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], None]

#: Publish progress at most this often. A window every second or two would otherwise put a socket
#: frame on the wire per window for an hour.
PROGRESS_INTERVAL_S = 1.0


class TranscriptionRunner:
    """Transcribes one finished recording on its own thread, publishing as it goes."""

    def __init__(
        self,
        *,
        registry: JobRegistry,
        emit: EmitFn,
        transcribe: Callable[..., Any],
        window_s: float = 30.0,
        overlap_s: float = 1.0,
        max_segment_s: float = 30.0,
        revision: int = 0,
        on_released: Callable[[], None] | None = None,
    ) -> None:
        self._registry = registry
        self._emit = emit
        #: Called once the store has been closed, so whoever handed it over stops reading from it.
        #: The transcript routes keep serving from that store *during* the pass — a reload
        #: mid-transcription must still show the segments already committed — which is only safe
        #: while someone is guaranteed to say when it goes away.
        self._on_released = on_released
        self._transcribe = transcribe
        self._window_s = window_s
        self._overlap_s = overlap_s
        self._max_segment_s = max_segment_s
        self._revision = revision

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_progress = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        *,
        job: TranscriptionJob,
        store: TranscriptStore,
        retain_audio: bool,
        prompt: str | None = None,
    ) -> bool:
        """Begin the pass. Returns False when another is already running.

        ``store`` is **handed over**: the runner closes it when the pass ends.
        """
        if not self._registry.claim(job):
            logger.warning("A transcription pass is already running; %s refused.", job.session_id)
            self._release(store)
            return False

        self._stop.clear()
        self._last_progress = 0.0
        self._thread = threading.Thread(
            target=self._run,
            args=(job, store, retain_audio, prompt),
            name="transcription-runner",
            daemon=True,
        )
        self._thread.start()
        self._emit("transcription.progress", job.as_event())
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the pass to end at the next window boundary and wait briefly for it.

        Called on shutdown. Deliberately does not wait for the whole pass: a server that takes half
        an hour to exit is a server nobody will let start automatically, and the partial transcript
        is already committed segment by segment.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # -- the pass itself -------------------------------------------------------------

    def _run(
        self,
        job: TranscriptionJob,
        store: TranscriptStore,
        retain_audio: bool,
        prompt: str | None,
    ) -> None:
        try:
            transcribe_file(
                job.source_path,
                transcribe=self._transcribe,
                window_s=self._window_s,
                overlap_s=self._overlap_s,
                max_segment_s=self._max_segment_s,
                first_segment_id=store.last_segment_id() + 1,
                revision=self._revision,
                prompt=prompt,
                on_progress=lambda seconds, segments: self._on_window(
                    job, store, seconds, segments
                ),
                should_stop=self._stop.is_set,
            )
        except BatchError as exc:
            self._fail(job, store, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - any failure must still release the slot
            logger.exception("Transcription of %s failed", job.source_path)
            self._fail(job, store, f"The transcription pass failed: {exc}")
            return

        job.finish()
        store.mark_ended()
        self._release(store)

        # Only once the transcript is safely written. Deleting earlier would make a failed pass
        # unrecoverable, and the audio is the only remaining copy of what was said.
        if not retain_audio:
            _discard(job.source_path)

        self._emit("transcription.done", job.as_event())
        logger.info(
            "Transcription of %s finished: %d segments.", job.source_path, job.segments_written
        )

    def _on_window(
        self,
        job: TranscriptionJob,
        store: TranscriptStore,
        seconds: float,
        segments: list[Segment],
    ) -> None:
        """Persist and publish one window's segments, then report progress."""
        for segment in segments:
            try:
                store.append_segment(segment)
            except Exception:  # noqa: BLE001 - a storage failure must not lose the rest
                logger.exception("Could not persist segment %s from the batch pass", segment.id)
            # Published even if the write failed, for the same reason the live path does: the
            # transcript on screen is worth having even when the disk is not cooperating.
            self._emit("transcript.committed", segment.as_event())

        job.advance(seconds, len(segments))

        now = time.monotonic()
        if now - self._last_progress >= PROGRESS_INTERVAL_S:
            self._last_progress = now
            self._emit("transcription.progress", job.as_event())

    def _release(self, store: TranscriptStore) -> None:
        """Close the store and tell its previous owner it is gone.

        The notification comes *after* the close, which leaves a window of microseconds in which a
        transcript request could hit a closed connection. Narrowing it further would mean holding a
        lock across every read on the hot path to save one possible 500 at the instant a pass ends,
        which is the wrong trade.
        """
        try:
            store.close()
        except Exception:  # noqa: BLE001 - closing a broken store must not mask the real failure
            logger.exception("Could not close the store after the pass")
        if self._on_released is not None:
            self._on_released()

    def _fail(self, job: TranscriptionJob, store: TranscriptStore, message: str) -> None:
        job.fail(message)
        self._release(store)

        # **The audio is kept, whatever the retention setting says.** It is now the only copy of
        # what was said, and the failure message names its path so the pass can be re-run.
        self._emit("transcription.failed", job.as_event())
        logger.error("Transcription of %s failed: %s", job.source_path, message)


def _discard(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
        logger.info("Recording %s deleted; audio retention is off.", path)
    except OSError as exc:
        # Not worth failing a successful transcription over.
        logger.warning("Could not delete the recording %s: %s", path, exc)
