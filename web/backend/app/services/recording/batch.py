"""Transcribe a finished recording in one pass (D-021), cut where the speaker paused (D-062).

**This deliberately does not use the streaming engine.** LocalAgreement exists to answer a question
that only arises while audio is still arriving: *what is safe to show now, given that the next
second might change it?* Once the file is complete that question is gone. Running the commit policy
over a finished recording would reproduce its two-to-four second latency and its trimming
heuristics for no benefit, and would discard exactly the context — the whole talk, at once — that
makes this mode worth having.

What it *does* reuse is the segmenter, so the segments produced here are indistinguishable from the
live path's: same shape, same id sequence, same sentence boundaries. That is what lets the store,
the FTS index, export, citations, and the polish pass work on a recorded transcript without knowing
it was produced differently.

**The pass walks the file in chunks that each end in a pause, not in windows on a clock.** The
first version used fixed thirty-second windows with a second of overlap reconciled by word
timestamp, and D-061 measured that against a real recording: the model wrote "..." over the phrase
each window had cut in half, and the overlap — reconciled by timestamps that jitter by a few
hundred milliseconds — dropped the boundary word from both windows or kept it in both. So the
recording is cut where nobody was speaking instead (`chunks.plan_chunks`), each chunk goes to the
model whole, and the chunks tile the file: nothing is transcribed twice and there is nothing to
merge. The only cut that can still land mid-word is the hard one taken when someone speaks through
the whole back half of a chunk without a breath, and the chunk says so.

The chunk is also the unit of the checkpoint (D-045). The chunks are planned over the *whole* file
before any is transcribed, from the same detector and the same settings, so a resumed pass plans
the same boundaries and picks up on one of them.
"""

from __future__ import annotations

import logging
import wave
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np

from ...models.segment import Segment
from ..asr.contract import WordToken
from ..audio.formats import SAMPLE_RATE
from ..streaming.segmenter import Segmenter
from ..vad.base import VoiceActivityDetector
from ..vad.energy import EnergyVad
from ..vad.pauses import find_pauses
from .chunks import Chunk, plan_chunks

logger = logging.getLogger(__name__)

#: Called with (transcribed_seconds, segments) after every chunk.
ProgressFn = Callable[[float, list[Segment]], None]
#: Signature of ``AsrLifecycle.transcribe``.
TranscribeFn = Callable[..., object]

#: The shortest silence that counts as somewhere to cut, when the caller does not say. A breath
#: between clauses; the gap between two words in a phrase is shorter.
DEFAULT_MIN_PAUSE_MS = 300
#: A pause at least this long is reported to the segmenter as a boundary, the way the live gate
#: reports one. Shorter pauses are still safe places to *cut* — no word straddles them — but a
#: breath mid-sentence is not the end of a thought, and telling the segmenter it was would split
#: sentences at every chunk seam. Matches ``VadConfig.pause_ms``.
DEFAULT_PAUSE_BOUNDARY_MS = 500


class BatchError(RuntimeError):
    """The recording could not be transcribed. The message names the file."""


def read_wav(path: Path | str) -> tuple[np.ndarray, float]:
    """Read a mono 16 kHz PCM WAV into canonical float32, with its duration.

    Reads through the standard library rather than a dependency: the only files this opens are
    ones :mod:`.sink` wrote, and the format is fixed and known.
    """
    source = Path(path)
    if not source.is_file():
        raise BatchError(f"The recording {source} no longer exists.")

    try:
        with wave.open(str(source), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, OSError) as exc:
        raise BatchError(f"The recording {source} could not be read: {exc}") from exc

    if width != 2:
        raise BatchError(f"The recording {source} is {width * 8}-bit; only 16-bit is supported.")

    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        raise BatchError(
            f"The recording {source} is {rate} Hz; the pipeline works at {SAMPLE_RATE} Hz."
        )

    return samples, samples.size / float(SAMPLE_RATE)


def plan_recording(
    samples: np.ndarray,
    *,
    detector: VoiceActivityDetector,
    chunk_s: float,
    min_pause_ms: int = DEFAULT_MIN_PAUSE_MS,
    start_s: float = 0.0,
) -> list[Chunk]:
    """Cut the recording at pauses, and drop every chunk that ends at or before ``start_s``.

    The chunks tile the recording — each begins where the previous ended — so what is left after
    the drop begins on a chunk boundary at or before ``start_s``. **A resume begins on a boundary,
    not mid-chunk (D-045).** Chunks are the unit the model sees and the unit a checkpoint records,
    so starting between two would either re-decode audio already committed or skip the part the
    previous chunk had not reached. The whole file is planned every time, from the same detector
    and the same settings, which is what makes the boundaries the same on both sides of a resume.
    """
    if samples.size == 0:
        return []
    pauses = find_pauses(samples, detector, min_pause_ms=min_pause_ms)
    chunks = plan_chunks(samples, pauses, max_chunk_s=chunk_s)
    if start_s <= 0:
        return chunks
    # Strictly after: a chunk ending exactly at the resume point was the one that wrote it.
    return [chunk for chunk in chunks if chunk.end_s > start_s + 1e-6]


def transcribe_file(
    path: Path | str,
    *,
    transcribe: TranscribeFn,
    detector: VoiceActivityDetector | None = None,
    chunk_s: float = 30.0,
    min_pause_ms: int = DEFAULT_MIN_PAUSE_MS,
    pause_boundary_ms: int = DEFAULT_PAUSE_BOUNDARY_MS,
    max_segment_s: float = 30.0,
    first_segment_id: int = 1,
    revision: int = 0,
    prompt: str | None = None,
    on_progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
    start_s: float = 0.0,
    on_chunk_start: Callable[[float, int], None] | None = None,
) -> list[Segment]:
    """Transcribe a finished recording and return its segments, in order.

    Args:
        transcribe: ``AsrLifecycle.transcribe``. Its hallucination filtering applies unchanged,
            which matters more here than live: a long recording contains far more silence than a
            talk anyone is watching, and silence is what makes a speech model invent text.
        detector: what decides where the recording is quiet. The energy detector when not given —
            dependency-free and deterministic — and the configured one, usually Silero, from the
            runner.
        chunk_s: the longest chunk handed to the model. A chunk ends earlier than this wherever a
            pause allows, so the real lengths vary; the model itself walks anything longer than
            thirty seconds in windows of its own, seeking to the end of its last complete segment.
        should_stop: consulted between chunks so a server shutdown does not have to wait for a
            forty-minute pass. Stopping returns what was produced so far rather than raising —
            partial transcript beats none.
        start_s: where to begin. Snapped back to a chunk boundary, so a resumed pass never
            re-decodes audio it already committed nor skips audio it did not (D-045).
        on_chunk_start: called with the second the next chunk begins at and the id the next
            segment will take, *before* that chunk is transcribed. This is the checkpoint, and it
            is written on every chunk rather than only on a pause: a pause can write its own, and
            a killed process cannot.
    """
    samples, duration = read_wav(path)
    segmenter = Segmenter(max_segment_s=max_segment_s, first_id=first_segment_id)
    produced: list[Segment] = []

    if samples.size == 0:
        logger.info("Recording %s contains no audio; nothing to transcribe.", path)
        return produced

    chunks = plan_recording(
        samples,
        detector=detector or EnergyVad(),
        chunk_s=chunk_s,
        min_pause_ms=min_pause_ms,
        start_s=start_s,
    )
    hard = sum(1 for chunk in chunks if not chunk.ends_at_pause)
    logger.info(
        "Recording %s (%.1f s) planned into %d chunk%s%s%s",
        path,
        duration,
        len(chunks),
        "" if len(chunks) == 1 else "s",
        f" from {start_s:.1f} s" if start_s > 0 else "",
        f"; {hard} cut mid-speech for want of a pause" if hard else "",
    )

    #: The end of the last chunk actually transcribed, and whether the loop was cut short. Both
    #: exist for the tail flush below, which otherwise reports the whole file's length as the
    #: position — walking a *held* pass to 100 % complete.
    reached = start_s
    stopped_early = False
    boundary_s = pause_boundary_ms / 1000.0

    for chunk in chunks:
        if should_stop is not None and should_stop():
            logger.info("Transcription of %s stopped early at %.1f s.", path, chunk.start_s)
            stopped_early = True
            break

        # Before the chunk, not after it: a checkpoint written afterwards names a position whose
        # work may not have been committed if the process died between the two.
        if on_chunk_start is not None:
            on_chunk_start(chunk.start_s, segmenter.next_id)

        result = transcribe(chunk.samples, prompt)
        words = _absolute_words(result, chunk)

        # The detector did run over this audio, so a pause long enough to end a thought is
        # reported as one — the same boundary the live gate would have raised. A shorter pause
        # was a safe place to cut and nothing more.
        segments = segmenter.add(
            words,
            model_id=getattr(result, "model_id", ""),
            pause_boundary=chunk.ends_at_pause and chunk.pause_s >= boundary_s,
        )
        # Stamped here rather than by the segmenter: which *pass* produced a segment is a fact
        # about this function's caller, and the segmenter is shared with the live path.
        segments = [replace(segment, revision=revision) for segment in segments]
        produced.extend(segments)
        reached = chunk.end_s
        if on_progress is not None:
            on_progress(chunk.end_s, segments)

    # The last words spoken are usually not followed by a full stop. Without this they are dropped,
    # which loses the end of every recording — the part a speaker most often uses for conclusions.
    tail = segmenter.flush()
    if tail is not None:
        tail = replace(tail, revision=revision)
        produced.append(tail)
        if on_progress is not None:
            # **`reached`, not `duration`, when the loop was stopped.** The tail is flushed either
            # way — partial transcript beats none — but reporting the whole file's length as the
            # position walks progress to 100 % for a pass that has been *held*, which showed in the
            # browser as "Held at 00:39:11 of 00:39:11 — 100 %" over a pass that had reached 65 %.
            # Found by pressing Pause and reading the label (D-045).
            on_progress(duration if not stopped_early else reached, [tail])

    logger.info("Transcribed %s: %.1f s of audio into %d segments.", path, duration, len(produced))
    return produced


def _absolute_words(result: object, chunk: Chunk) -> list[WordToken]:
    """Rebase a chunk's words onto the recording's timeline.

    Word times come back relative to the array that was submitted. Every consumer downstream —
    the store, citations, export, the polish pass — assumes times relative to the *session*, so
    the rebasing has to happen here and exactly once. Nothing is dropped: the chunks do not
    overlap, so every word the model returned was said inside this chunk.
    """
    words = getattr(result, "words", None) or []
    return [
        WordToken(
            text=word.text,
            start=chunk.start_s + word.start,
            end=chunk.start_s + word.end,
            confidence=word.confidence,
        )
        for word in words
    ]
