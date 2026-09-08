"""The words of a dictation, a chunk at a time (D-061).

A dictation used to go through the batch pass in `recording/batch.py`: thirty-second windows on a
clock, a second of overlap, and a merge by word timestamp. Measured against a real two-and-a-half
minute dictation that gave "...", where a window had cut a phrase in half, plus three duplicated
phrases at the other boundaries. The tidy pass repaired the duplicates without being asked and
could not repair the gap, because the words were simply not there.

So the file is cut where the speaker paused instead. `find_pauses` marks the silences,
`plan_chunks` ends every chunk inside one, and each chunk goes to the model **whole** — Whisper's
own transcribe call walks anything longer than thirty seconds by seeking to the end of its last
complete segment, which is a pause-aware cut of its own. There is no overlap and nothing to merge:
the chunks tile the recording and their transcripts are joined in order.

The chunk is also the unit the tidy works on. The language model was measured at under a second
for twenty-five seconds of speech, so a two-minute chunk sits well inside the timeout — while a
twenty-minute dictation tidied in one request would blow through it and paste raw. Each chunk gets
the whole timeout to itself, and a chunk the model mangles falls back to its own raw text alone.

And the tidy of one chunk runs while the next is still being transcribed (D-063). The two models
are different resources, so the only tidy the user waits for is the last chunk's.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...config.schema import AppConfig
from ..recording.batch import read_wav
from ..recording.chunks import Chunk, plan_chunks
from ..vad.base import VoiceActivityDetector
from ..vad.pauses import find_pauses
from . import prompts

logger = logging.getLogger(__name__)

#: Called with (chunk index, chunk count) before each chunk is worked on.
ProgressFn = Callable[[int, int], None]


def cut_at_pauses(
    path: Path | str, config: AppConfig, detector: VoiceActivityDetector
) -> list[Chunk]:
    """Read a dictation's recording and cut it into chunks that each end in silence."""
    samples, duration = read_wav(path)
    if samples.size == 0:
        return []
    pauses = find_pauses(samples, detector, min_pause_ms=config.dictation.min_pause_ms)
    chunks = plan_chunks(samples, pauses, max_chunk_s=config.dictation.chunk_seconds)
    hard = sum(1 for chunk in chunks if not chunk.ends_at_pause)
    logger.info(
        "Dictation of %.1f s cut into %d chunk%s at %d pause%s%s",
        duration,
        len(chunks),
        "" if len(chunks) == 1 else "s",
        len(pauses),
        "" if len(pauses) == 1 else "s",
        f"; {hard} cut mid-speech for want of one" if hard else "",
    )
    return chunks


@dataclass
class Delivered:
    """What the pipeline produced, and how long each half of it took."""

    #: The raw text of every chunk that said anything, in order.
    raws: list[str]
    #: The text to paste: each chunk tidied where the tidy succeeded, raw where it did not.
    texts: list[str]
    #: Whether every chunk was tidied. False when the tidy was off, refused, or overran.
    all_tidied: bool
    #: Wall-clock seconds the transcription loop took.
    transcribe_s: float
    #: Wall-clock seconds spent waiting for the tidy *after* the last chunk was transcribed. The
    #: tidy of every earlier chunk ran while a later one was still being transcribed, so this is
    #: the only part of the tidy the user actually waited for (D-063).
    tidy_wait_s: float

    @property
    def raw(self) -> str:
        return " ".join(self.raws)

    @property
    def text(self) -> str:
        return " ".join(self.texts)


def transcribe_and_tidy(
    chunks: list[Chunk],
    transcribe: Callable[..., Any],
    config: AppConfig,
    backend_factory: Callable[[], Any],
    *,
    tidy: bool,
    on_transcribed: Callable[[int, int], None] | None = None,
    on_tidied: Callable[[int, int], None] | None = None,
    on_transcription_done: Callable[[], None] | None = None,
) -> Delivered:
    """Every chunk through the model in order, each tidied as soon as it has been transcribed.

    **The two halves overlap (D-063).** The speech model and the language model are different
    resources — on this machine one runs on the CPU and the other answers over HTTP — so tidying
    chunk one while chunk two transcribes costs nothing and, on a long dictation, roughly halves
    the wait. The transcription runs on the calling thread; the tidy runs on one worker that
    consumes the chunks in order, so the texts come back in the order they were spoken and the
    give-up rule in :func:`tidy_one` — a timeout or a refusal stops the tidy for every chunk after
    it — applies exactly as it did when the two ran in sequence.

    ``on_transcribed`` and ``on_tidied`` are each called with ``(index, count)``; the count of
    chunks to tidy is not known until the transcription has finished, so ``on_tidied`` is given the
    number of chunks in the recording. ``on_transcription_done`` is called once, when the last
    chunk has been transcribed and only the tidy remains.
    """
    raws: list[str] = []
    texts: dict[int, str] = {}
    tidied: dict[int, bool] = {}
    pending: queue.Queue[tuple[int, str] | None] = queue.Queue()

    def worker() -> None:
        give_up = False
        while (item := pending.get()) is not None:
            index, raw = item
            if give_up:
                texts[index], tidied[index] = raw, False
                continue
            if on_tidied is not None:
                on_tidied(index, len(chunks))
            text, ok, keep_going = tidy_one(raw, config, backend_factory)
            texts[index], tidied[index] = text, ok
            give_up = not keep_going

    thread: threading.Thread | None = None
    if tidy:
        thread = threading.Thread(target=worker, name="dictation-tidy", daemon=True)
        thread.start()

    started = time.monotonic()
    for chunk in chunks:
        if on_transcribed is not None:
            on_transcribed(chunk.index, len(chunks))
        text = _text_of(transcribe(chunk.samples, None))
        if not text:
            continue
        raws.append(text)
        if thread is not None:
            pending.put((len(raws) - 1, text))
    transcribed_at = time.monotonic()
    if on_transcription_done is not None:
        on_transcription_done()

    if thread is None:
        return Delivered(raws, list(raws), False, transcribed_at - started, 0.0)

    pending.put(None)
    thread.join()
    return Delivered(
        raws=raws,
        texts=[texts[index] for index in range(len(raws))],
        all_tidied=bool(raws) and all(tidied[index] for index in range(len(raws))),
        transcribe_s=transcribed_at - started,
        tidy_wait_s=time.monotonic() - transcribed_at,
    )


def tidy_one(
    raw: str, config: AppConfig, backend_factory: Callable[[], Any]
) -> tuple[str, bool, bool]:
    """One chunk through the language model. Returns ``(text, tidied, keep_going)``.

    Falls back to ``raw`` rather than waiting: the tidy is bounded by ``cleanup_timeout_s``, and
    whichever text wins is what gets pasted, exactly once.
    """
    from ...services.llm.contract import GenerationOptions, system, user

    async def ask() -> str:
        backend = backend_factory()
        options = GenerationOptions(
            temperature=0.0,
            max_output_tokens=max(256, len(raw.split()) * 6),
            extra=dict(prompts.NO_REASONING_EXTRAS),
        )
        return await backend.complete([system(prompts.DICTATION_PROMPT), user(raw)], options)

    async def bounded() -> str:
        return await asyncio.wait_for(ask(), timeout=config.dictation.cleanup_timeout_s)

    try:
        cleaned = asyncio.run(bounded()).strip()
    except TimeoutError:
        logger.info(
            "The tidy pass overran %.0f s; pasting what was said instead",
            config.dictation.cleanup_timeout_s,
        )
        return raw, False, False
    except Exception as exc:  # noqa: BLE001 - no model, no server, a refusal
        logger.info("Could not tidy the dictation (%s); pasting what was said", exc)
        return raw, False, False

    if not cleaned:
        return raw, False, True
    # **A guard, not a formality.** A model that answers a punctuation request with a paragraph
    # of its own has not tidied anything, and pasting that into someone's document is the worst
    # outcome this feature has. Compare word counts, the same check the polish pass makes.
    spoken, written = len(raw.split()), len(cleaned.split())
    if written > spoken * 2 + 8 or written < spoken * 0.5:
        logger.info(
            "The tidy pass returned %d words for %d; keeping what was said", written, spoken
        )
        return raw, False, True
    return cleaned, True, True


def _text_of(result: Any) -> str:
    """The recognised text of one pass, whatever shape the backend returned it in."""
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return " ".join(text.split())
    words = getattr(result, "words", None) or []
    return " ".join(str(getattr(word, "text", "")).strip() for word in words).strip()


__all__ = ["Delivered", "ProgressFn", "cut_at_pauses", "tidy_one", "transcribe_and_tidy"]
