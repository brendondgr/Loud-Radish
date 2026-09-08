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
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
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


def transcribe_chunks(
    chunks: list[Chunk], transcribe: Callable[..., Any], on_progress: ProgressFn | None = None
) -> list[str]:
    """Each chunk through the model whole, in order. Chunks that said nothing are dropped."""
    texts: list[str] = []
    for chunk in chunks:
        if on_progress is not None:
            on_progress(chunk.index, len(chunks))
        result = transcribe(chunk.samples, None)
        text = _text_of(result)
        if text:
            texts.append(text)
    return texts


def tidy_chunks(
    raws: list[str],
    config: AppConfig,
    backend_factory: Callable[[], Any],
    on_progress: ProgressFn | None = None,
) -> tuple[list[str], bool]:
    """Punctuation and capitalisation for each chunk, each bounded. Returns the texts and whether
    every one of them was tidied.

    **A timeout or a refusal stops the tidy for the chunks that follow.** Those are properties of
    the server, not of the chunk: a model that has stalled on the first two minutes will stall on
    the next eight, and waiting the full timeout for each would turn a keystroke into a coffee
    break — which is the exact failure the timeout exists to prevent. A chunk that comes back with
    an essay instead of punctuation is the model's opinion of *that* chunk, so the next one is
    still tried.
    """
    texts: list[str] = []
    all_tidied = True
    give_up = False
    for index, raw in enumerate(raws):
        if give_up:
            texts.append(raw)
            all_tidied = False
            continue
        if on_progress is not None:
            on_progress(index, len(raws))
        text, tidied, keep_going = tidy_one(raw, config, backend_factory)
        texts.append(text)
        all_tidied = all_tidied and tidied
        give_up = not keep_going
    return texts, all_tidied


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


__all__ = ["ProgressFn", "cut_at_pauses", "tidy_chunks", "tidy_one", "transcribe_chunks"]
