"""Cut a finished recording into pieces where nobody was speaking (D-061).

`batch.py` walks a file in fixed windows with a second of overlap and reconciles the two
transcriptions of that second by word timestamp. Measured against a real dictation, that loses at
every boundary: Whisper writes "..." over a phrase the window cut in half, and a word whose
timestamp jitters across the overlap edge is dropped by both windows or kept by both. Four
boundaries in a two-and-a-half minute dictation gave one gap and three duplicates.

This module makes the boundary problem disappear rather than reconciling it. Every chunk ends in a
pause, so there is no half-word for the model to see and no overlap for anything to merge: the
chunks tile the recording exactly, and their transcripts are simply joined in order.

**The longest pause in the back half of each chunk, not the last one before the limit.** A breath
between words is a pause too, and the last such gap before the limit is as likely to sit between
"the" and "matrix" as between two sentences. The longest gap in a long enough stretch is far more
often a sentence boundary — and cutting there costs nothing but a somewhat shorter chunk.

The only cut that can still fall mid-word is the hard one taken when no pause at all exists in
the back half of a chunk, which means someone spoke for a minute without a three-hundred
millisecond breath. That case is marked on the chunk rather than hidden.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from ..audio.formats import SAMPLE_RATE
from ..vad.pauses import Pause

#: A remainder shorter than this after the last cut is folded into the chunk before it. Handing a
#: speech model half a second of trailing silence wastes a pass and invites it to invent "you".
MIN_TAIL_S = 1.0


@dataclass(frozen=True)
class Chunk:
    """One piece of a recording, handed to the model whole."""

    index: int
    #: Seconds from the start of the recording.
    start_s: float
    end_s: float
    samples: np.ndarray
    #: Whether the cut that ends this chunk fell inside a pause. The last chunk ends at the end of
    #: the recording, which counts. ``False`` is the hard cut described in the module docstring.
    ends_at_pause: bool
    #: How long that pause was, in seconds. Zero for a hard cut and for the end of the recording.
    #: A caller deciding whether the pause ended a *thought* — rather than merely being a safe
    #: place to cut — reads this rather than ``ends_at_pause`` (D-062).
    pause_s: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def plan_chunks(
    samples: np.ndarray,
    pauses: Iterable[Pause],
    *,
    max_chunk_s: float,
    sample_rate: int = SAMPLE_RATE,
) -> list[Chunk]:
    """Slice ``samples`` into chunks no longer than ``max_chunk_s``, cut at pauses where possible.

    The chunks tile the recording: each begins where the previous one ended, and the last ends at
    the end of the file. Nothing is transcribed twice and nothing is skipped.
    """
    array = np.asarray(samples, dtype=np.float32).ravel()
    duration = array.size / float(sample_rate)
    if array.size == 0:
        return []
    if max_chunk_s <= 0:
        raise ValueError(f"max_chunk_s must be positive, got {max_chunk_s}")

    ordered = sorted(pauses, key=lambda pause: pause.start_s)
    chunks: list[Chunk] = []
    start = 0.0

    while True:
        remaining = duration - start
        if remaining <= max_chunk_s:
            chunks.append(_chunk(len(chunks), start, duration, array, sample_rate, at_pause=True))
            return chunks

        limit = start + max_chunk_s
        cut, pause = _best_cut(ordered, start + max_chunk_s / 2.0, limit)

        # A sliver left after the cut is not worth a pass of its own. Folding it in can only
        # lengthen this chunk by under a second, since the cut was at or before the limit.
        if duration - cut < MIN_TAIL_S:
            chunks.append(_chunk(len(chunks), start, duration, array, sample_rate, at_pause=True))
            return chunks

        chunks.append(
            _chunk(
                len(chunks),
                start,
                cut,
                array,
                sample_rate,
                at_pause=pause is not None,
                pause_s=pause.duration_s if pause is not None else 0.0,
            )
        )
        start = cut


def _best_cut(pauses: list[Pause], region_start: float, limit: float) -> tuple[float, Pause | None]:
    """Where to end a chunk that must end by ``limit``: inside a pause if one can be found.

    Candidates are the pauses overlapping the back half of the chunk. The longest wins; among
    equals the later one, because a longer chunk means fewer cuts. The cut lands at the pause's
    midpoint, pulled inside the region if the pause runs past either edge of it. Returns the cut
    and the pause it fell in, or ``None`` for the hard cut at the limit.
    """
    best: Pause | None = None
    for pause in pauses:
        if pause.end_s <= region_start or pause.start_s >= limit:
            continue
        if best is None or (pause.duration_s, pause.start_s) >= (best.duration_s, best.start_s):
            best = pause

    if best is None:
        return limit, None

    low = max(best.start_s, region_start)
    high = min(best.end_s, limit)
    return min(max(best.middle_s, low), high), best


def _chunk(
    index: int,
    start_s: float,
    end_s: float,
    array: np.ndarray,
    sample_rate: int,
    *,
    at_pause: bool,
    pause_s: float = 0.0,
) -> Chunk:
    first = int(round(start_s * sample_rate))
    last = int(round(end_s * sample_rate))
    return Chunk(
        index=index,
        start_s=first / sample_rate,
        end_s=last / sample_rate,
        samples=array[first:last],
        ends_at_pause=at_pause,
        pause_s=pause_s,
    )


__all__ = ["MIN_TAIL_S", "Chunk", "plan_chunks"]
