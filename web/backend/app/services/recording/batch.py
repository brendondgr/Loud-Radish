"""Transcribe a finished recording in one pass (D-021).

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

The pass walks the file in overlapping windows. Overlap matters: a word split across a boundary is
transcribed as two fragments by both windows, and taking the second window's words only from the
point where the first window ended keeps whichever one saw it whole.
"""

from __future__ import annotations

import logging
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ...models.segment import Segment
from ..asr.contract import WordToken
from ..audio.formats import SAMPLE_RATE
from ..streaming.segmenter import Segmenter

logger = logging.getLogger(__name__)

#: Called with (transcribed_seconds, segments) after every window.
ProgressFn = Callable[[float, list[Segment]], None]
#: Signature of ``AsrLifecycle.transcribe``.
TranscribeFn = Callable[..., object]


class BatchError(RuntimeError):
    """The recording could not be transcribed. The message names the file."""


@dataclass(frozen=True)
class Window:
    """One slice of the recording handed to the model."""

    index: int
    #: Seconds from the start of the recording.
    start_s: float
    end_s: float
    samples: np.ndarray
    #: Words starting before this offset *within the window* were already covered by the previous
    #: window's non-overlapping part, and are dropped rather than emitted twice.
    keep_from_s: float


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


def plan_windows(
    samples: np.ndarray,
    *,
    window_s: float,
    overlap_s: float,
    start_s: float = 0.0,
) -> Iterator[Window]:
    """Slice the recording into overlapping windows, in order.

    The overlap is *context for the model*, not extra transcript: each window's words are kept only
    from where the previous window stopped, so nothing is emitted twice.

    **The overlap is capped at half the window**, and that cap is load-bearing rather than tidiness.
    Clamping it only to ``window - 1`` still terminates, but an overlap larger than the window
    leaves a step of one sample: a six-second recording planned into sixty-four thousand windows,
    each a full inference pass. Half a window is also the point past which the overlap stops being
    context and starts being the majority of the audio, transcribed twice for nothing.
    """
    total = samples.size
    if total == 0:
        return

    window = max(1, int(window_s * SAMPLE_RATE))
    overlap = max(0, min(int(overlap_s * SAMPLE_RATE), window // 2))
    step = max(1, window - overlap)

    # **A resume begins on a window boundary, not mid-window (D-045).** Windows are the unit the
    # model sees and the unit a checkpoint records, so starting between two would either re-decode
    # audio already committed or skip the part of it the previous window had not reached. Snapping
    # to the step grid keeps the overlap doing its job — a resumed window still sees the second of
    # context before it — and costs at most one window of work.
    index = 0
    start = 0
    if start_s > 0:
        offset = max(0, int(start_s * SAMPLE_RATE))
        index = max(0, offset // step)
        start = index * step
        if start >= total:
            return
    while start < total:
        end = min(start + window, total)
        yield Window(
            index=index,
            start_s=start / SAMPLE_RATE,
            end_s=end / SAMPLE_RATE,
            samples=samples[start:end],
            # The first window keeps everything; later ones keep only what the previous one did
            # not already cover.
            keep_from_s=0.0 if index == 0 else overlap / SAMPLE_RATE,
        )
        if end >= total:
            return
        start += step
        index += 1


def transcribe_file(
    path: Path | str,
    *,
    transcribe: TranscribeFn,
    window_s: float = 30.0,
    overlap_s: float = 1.0,
    max_segment_s: float = 30.0,
    first_segment_id: int = 1,
    revision: int = 0,
    prompt: str | None = None,
    on_progress: ProgressFn | None = None,
    should_stop: Callable[[], bool] | None = None,
    start_s: float = 0.0,
    on_window_start: Callable[[float, int], None] | None = None,
) -> list[Segment]:
    """Transcribe a finished recording and return its segments, in order.

    Args:
        transcribe: ``AsrLifecycle.transcribe``. Its hallucination filtering applies unchanged,
            which matters more here than live: a long recording contains far more silence than a
            talk anyone is watching, and silence is what makes a speech model invent text.
        should_stop: consulted between windows so a server shutdown does not have to wait for a
            forty-minute pass. Stopping returns what was produced so far rather than raising —
            partial transcript beats none.
        start_s: where to begin. Snapped to a window boundary, so a resumed pass never re-decodes
            audio it already committed nor skips audio it did not (D-045).
        on_window_start: called with the second the next window begins at and the id the next
            segment will take, *before* that window is transcribed. This is the checkpoint, and it
            is written on every window rather than only on a pause: a pause can write its own, and
            a killed process cannot.
    """
    samples, duration = read_wav(path)
    segmenter = Segmenter(max_segment_s=max_segment_s, first_id=first_segment_id)
    produced: list[Segment] = []

    if samples.size == 0:
        logger.info("Recording %s contains no audio; nothing to transcribe.", path)
        return produced

    for window in plan_windows(samples, window_s=window_s, overlap_s=overlap_s, start_s=start_s):
        if should_stop is not None and should_stop():
            logger.info("Transcription of %s stopped early at %.1f s.", path, window.start_s)
            break

        # Before the window, not after it: a checkpoint written afterwards names a position whose
        # work may not have been committed if the process died between the two.
        if on_window_start is not None:
            on_window_start(window.start_s, segmenter.next_id)

        result = transcribe(window.samples, prompt)
        words = _absolute_words(result, window)

        # No pause boundary: the VAD did not run over this audio, so the only boundaries available
        # are punctuation and the duration cap. Claiming a pause we did not detect would split
        # sentences at arbitrary points.
        segments = segmenter.add(words, model_id=getattr(result, "model_id", ""))
        # Stamped here rather than by the segmenter: which *pass* produced a segment is a fact
        # about this function's caller, and the segmenter is shared with the live path.
        segments = [replace(segment, revision=revision) for segment in segments]
        produced.extend(segments)
        if on_progress is not None:
            on_progress(window.end_s, segments)

    # The last words spoken are usually not followed by a full stop. Without this they are dropped,
    # which loses the end of every recording — the part a speaker most often uses for conclusions.
    tail = segmenter.flush()
    if tail is not None:
        tail = replace(tail, revision=revision)
        produced.append(tail)
        if on_progress is not None:
            on_progress(duration, [tail])

    logger.info("Transcribed %s: %.1f s of audio into %d segments.", path, duration, len(produced))
    return produced


def _absolute_words(result: object, window: Window) -> list[WordToken]:
    """Rebase a window's words onto the recording's timeline, dropping the overlap's duplicates.

    Word times come back relative to the array that was submitted. Every consumer downstream —
    the store, citations, export, the polish pass — assumes times relative to the *session*, so
    the rebasing has to happen here and exactly once.
    """
    words = getattr(result, "words", None) or []
    kept: list[WordToken] = []
    for word in words:
        if word.start < window.keep_from_s:
            continue
        kept.append(
            WordToken(
                text=word.text,
                start=window.start_s + word.start,
                end=window.start_s + word.end,
                confidence=word.confidence,
            )
        )
    return kept
