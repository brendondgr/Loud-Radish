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

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...models.segment import Segment
from ..transcript import TranscriptStore
from .batch import BatchError, transcribe_file
from .characterise import characterise
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
        # **Distinct from `_stop`, and that distinction is the whole feature.** Shutdown and pause
        # both end the loop at a window boundary, but they mean opposite things afterwards: a
        # shutdown leaves a pass to be resumed by the next process, a pause leaves one to be
        # resumed by the user, and a cancel leaves one that must not be resumed at all. One flag
        # could not tell the runner which terminal state to write (D-045).
        self._hold = threading.Event()
        self._cancel = threading.Event()
        self._last_progress = 0.0
        #: Set while a resumed pass has not yet reached its first window, so the trim can happen at
        #: the real boundary rather than the requested one.
        self._resumed_from: float | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def pause(self) -> None:
        """Ask the pass to hold at the next window boundary. Returns immediately.

        The thread finishes the window it is inside — windows are atomic, and abandoning one
        mid-decode would lose its work without recording that it had been started.
        """
        self._hold.set()

    def resume(
        self,
        *,
        job: TranscriptionJob,
        store: TranscriptStore,
        retain_audio: bool,
        prompt: str | None = None,
    ) -> bool:
        """Pick a held pass up from its checkpoint."""
        job.resume()
        return self.start(
            job=job, store=store, retain_audio=retain_audio, prompt=prompt, resuming=True
        )

    def cancel(self) -> None:
        """End the pass for good. What was transcribed stays committed; the audio stays on disk."""
        self._cancel.set()
        self._hold.set()

    def start(
        self,
        *,
        job: TranscriptionJob,
        store: TranscriptStore,
        retain_audio: bool,
        prompt: str | None = None,
        resuming: bool = False,
    ) -> bool:
        """Begin the pass. Returns False when another is already running.

        ``store`` is **handed over**: the runner closes it when the pass ends.
        """
        if not self._registry.claim(job, resuming=resuming):
            logger.warning("A transcription pass is already running; %s refused.", job.session_id)
            self._release(store)
            return False

        # The key is a fact about the store, and only the runner sees both. A client needs it to
        # address the resume, which goes to the session rather than the recording.
        job.key = job.key or store.path.stem
        self._stop.clear()
        self._hold.clear()
        self._cancel.clear()
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

    def _should_stop(self) -> bool:
        """Consulted between windows. Any of the three reasons ends the loop the same way."""
        return self._stop.is_set() or self._hold.is_set() or self._cancel.is_set()

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
        # **The trim happens at the first window, not here**, because *here* does not yet know
        # where the first window actually is: `plan_windows` snaps a resume back to the boundary at
        # or before the requested second, so asking to resume at 540 s can genuinely begin at 522 s.
        # Trimming at 540 and then transcribing from 522 re-derives 522-540 on top of segments that
        # were kept — which is exactly the duplication the trim exists to prevent, and it is what a
        # real interrupted pass produced before this was moved.
        self._resumed_from = job.next_start_s if job.next_start_s > 0 else None
        first_id = store.last_segment_id() + 1
        self._checkpoint(job, store, prompt, state="running")
        try:
            transcribe_file(
                job.source_path,
                transcribe=self._transcribe,
                window_s=self._window_s,
                overlap_s=self._overlap_s,
                max_segment_s=self._max_segment_s,
                first_segment_id=first_id,
                revision=self._revision,
                prompt=prompt,
                on_progress=lambda seconds, segments: self._on_window(
                    job, store, seconds, segments
                ),
                should_stop=self._should_stop,
                start_s=job.next_start_s,
                on_window_start=lambda start_s, next_id: self._on_window_start(
                    job, store, prompt, start_s, next_id
                ),
            )
        except BatchError as exc:
            self._fail(job, store, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - any failure must still release the slot
            logger.exception("Transcription of %s failed", job.source_path)
            self._fail(job, store, f"The transcription pass failed: {exc}")
            return

        # The three ways a pass can end early, before the ways it can end well.
        if self._cancel.is_set():
            job.cancel()
            self._checkpoint(job, store, prompt, state="cancelled")
            self._release(store)
            self._emit("transcription.cancelled", job.as_event())
            logger.info(
                "Transcription of %s cancelled at %.1f s", job.source_path, job.next_start_s
            )
            return
        if self._hold.is_set():
            job.pause()
            self._checkpoint(job, store, prompt, state="paused")
            self._release(store)
            self._emit("transcription.paused", job.as_event())
            logger.info("Transcription of %s held at %.1f s", job.source_path, job.next_start_s)
            return
        if self._stop.is_set():
            # Shutdown. The checkpoint is already on disk from the last window, and the state stays
            # `running` on purpose: that is what the next process reads as "this was interrupted",
            # which is exactly what happened.
            self._release(store)
            logger.info(
                "Transcription of %s interrupted at %.1f s", job.source_path, job.next_start_s
            )
            return

        # **Measured before the outcome is named.** "322 seconds processed, 2 segments" is
        # unfalsifiable from outside — it is what a broken transcriber looks like *and* what an
        # accurate one looks like on music. The sidecar and the terminal state together make it
        # answerable by reading a file instead of by an afternoon with ffmpeg and numpy.
        job.audio = self._characterise(job)
        found_speech = job.segments_written > 0
        job.finish(found_speech=found_speech)
        self._checkpoint(job, store, prompt, state="done")
        self._write_sidecar(job)
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

    def _characterise(self, job: TranscriptionJob) -> dict[str, Any]:
        """Measure the recording's audio. Never fatal — a failed measurement loses a diagnostic."""
        try:
            return characterise(job.source_path).as_dict()
        except Exception:  # noqa: BLE001 - a diagnostic must never fail the pass it describes
            logger.exception("Could not characterise %s", job.source_path)
            return {}

    def _write_sidecar(self, job: TranscriptionJob) -> None:
        """Write the measurements beside the recording, as its own artefact.

        Research §9.2: once every recording carries its own measured dimensions, levels, spectrum
        and speech ratio, a whole class of "it seemed to work" becomes a diff between two JSON
        documents. Written next to the audio rather than into the database because it describes the
        *file*, and it should survive the session being deleted.
        """
        source = Path(job.source_path)
        payload = {
            "session_id": job.session_id,
            "source": source.name,
            "audio": job.audio,
            "transcription": {
                "state": str(job.state),
                "segments": job.segments_written,
                "total_seconds": round(job.total_seconds, 2),
                "transcribed_seconds": round(job.transcribed_seconds, 2),
                "error": job.error,
            },
        }
        try:
            source.with_suffix(".json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Could not write the measurement sidecar for %s", source)

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

    def _on_window_start(
        self,
        job: TranscriptionJob,
        store: TranscriptStore,
        prompt: str | None,
        start_s: float,
        next_id: int,
    ) -> None:
        """Record where a resume would begin, before the window is decoded.

        The first call of a resumed pass also trims: this is the first moment the *real* first
        window is known, and everything from it onward is about to be re-derived from audio that is
        still on disk. Nothing before it is touched, so the transcript already read does not change.
        """
        if self._resumed_from is not None:
            self._resumed_from = None
            store.trim_from(self._revision, start_s)
        job.checkpoint(start_s, next_id)
        self._checkpoint(job, store, prompt, state="running")

    def _checkpoint(
        self, job: TranscriptionJob, store: TranscriptStore, prompt: str | None, *, state: str
    ) -> None:
        """Write the pass's position into its own transcript database. Never fatal.

        A checkpoint that cannot be written costs the ability to resume; raising here would cost
        the pass itself, which is the more expensive of the two by a wide margin.
        """
        try:
            store.record_pass(
                revision=self._revision,
                source_path=job.source_path,
                total_seconds=job.total_seconds,
                state=state,
                next_start_s=job.next_start_s,
                last_segment_id=job.next_segment_id,
                prompt=prompt or "",
            )
        except Exception:  # noqa: BLE001 - a lost checkpoint must not lose the transcription
            logger.debug("Could not write the transcription checkpoint", exc_info=True)

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
        self._checkpoint(job, store, None, state="failed")
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
