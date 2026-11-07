"""Short titles for recordings, written by the language model once a transcript is finished (D-084).

Three hundred recordings all called "Session on <date>" cannot be told apart. The recordings lab
named them by their most-used words (``glance.py``), which proved the idea and showed its limits
("Operators · Commute"); the owner asked for a four-to-five-word title written on transcription.

**When it runs.** Once per recording, after its transcript is complete: when a live session stops,
or when a post-capture pass finishes. Never while a session is still transcribing, and never over a
title that already exists — one the user typed or corrected wins. Older recordings are named only
when the owner asks (the *backfill*), because nothing here processes a recording unasked.

**The GPU.** A local model shares the iGPU with the speech model, and the two running together
caused the 2026-09-18 crashes (D-071 to D-074). The request goes through the same client as every
other, which already takes its turn on the GPU (D-072); on top of that, titles are written one at a
time, so a backfill of three hundred is three hundred short turns rather than a queue of them.

**What the model is shown.** The session's summaries when it has them — they are short and already
say what the talk is about — otherwise the opening of the transcript. Background naming runs only
when rolling summaries are on (``context.summary_enabled``), which is the owner's existing consent
for the model to read a transcript in the background.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..context import prompts
from ..llm.contract import GenerationOptions, LlmBackend, system, user
from ..llm.errors import LlmError
from . import archive
from .glance import Glance
from .store import TranscriptStore

logger = logging.getLogger(__name__)

#: Below this a recording has too little said to name; its card shows keywords instead.
MIN_WORDS = 40
#: How much of the transcript the model reads when there are no summaries.
SOURCE_WORDS = 600
#: How many summaries it reads when there are some — the opening of a long talk says what it is.
SOURCE_SUMMARIES = 4
MAX_CHARS = 60
#: A title is a handful of words; a model that runs on is answering something else.
MAX_WORDS = 10
TITLE_TOKENS = 48

_PREFIX = re.compile(r"^\s*(?:title|name|recording)\s*[:\-–—]\s*", re.I)
_MARKUP = re.compile(r"[*_#`>\"“”‘’«»]+")
_REFUSAL = re.compile(r"^(?:i |i'm |sorry|as an ai|here is|here's|the transcript)", re.I)


def clean_title(raw: str) -> str:
    """The model's answer as a title, or ``""`` when it is not one.

    Takes the first line, drops quotes, markup, a "Title:" prefix and trailing punctuation, and
    refuses an answer that is a sentence about the task rather than a name.
    """
    line = next((part for part in (raw or "").splitlines() if part.strip()), "")
    line = _MARKUP.sub("", _PREFIX.sub("", line))
    line = " ".join(line.split()).strip(" '.,;:!?-–—")
    words = line.split()
    if len(words) < 2 or len(words) > MAX_WORDS or not re.search(r"[A-Za-z]", line):
        return ""
    if _REFUSAL.match(line):
        return ""
    if len(line) > MAX_CHARS:
        line = line[:MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return line


def source_text(store: TranscriptStore) -> str:
    """What the model reads to name the session, or ``""`` when too little was said."""
    segments = store.segments_for(None) or []
    words = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).split()
    if len(words) < MIN_WORDS:
        return ""
    summaries = [summary.text.strip() for summary in store.summaries() if summary.text.strip()]
    if summaries:
        return "\n\n".join(summaries[:SOURCE_SUMMARIES])
    return " ".join(words[:SOURCE_WORDS])


async def suggest_title(text: str, backend: LlmBackend) -> str:
    """Ask the model to name what ``text`` is about. ``""`` when its answer is not a title."""
    if not text:
        return ""
    raw = await backend.complete(
        [system(prompts.TITLE_PROMPT), user(text)],
        GenerationOptions(temperature=0.2, max_output_tokens=TITLE_TOKENS),
    )
    title = clean_title(raw)
    if not title:
        logger.info("The model's answer was not a title: %r", raw[:80])
    return title


def keyword_title(found: Glance) -> str:
    """The fallback a card shows until a title exists. Never stored."""
    return " · ".join(found.keywords[:3])


@dataclass
class Backfill:
    """Naming the recordings that have no title, on the owner's approval."""

    running: bool = False
    total: int = 0
    done: int = 0
    named: int = 0
    current: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "total": self.total,
            "done": self.done,
            "named": self.named,
            "current": self.current,
        }


class TitleService:
    """Writes titles one at a time, for a stopped session, a finished pass, or the backfill."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], Any],
        backend_factory: Callable[[], LlmBackend],
        emit: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._config = config_provider
        self._backend_factory = backend_factory
        self._emit = emit
        self._loop: asyncio.AbstractEventLoop | None = None
        self._turn: asyncio.Lock | None = None
        self._pending: set[str] = set()
        self._pending_lock = threading.Lock()
        self.backfill = Backfill()
        self._backfill_task: asyncio.Task[None] | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def model(self) -> str:
        """The model titles would be written by, or ``""`` when none is configured."""
        try:
            return self._backend_factory().model_id or ""
        except LlmError:
            return ""

    # -- after a transcript is finished -----------------------------------------------------

    def request(self, key: str) -> None:
        """Name ``key`` in the background if it has no title. **Safe to call from any thread.**"""
        loop = self._loop
        if not key or loop is None or loop.is_closed():
            return
        if not self._config().context.summary_enabled:
            return
        with self._pending_lock:
            if key in self._pending:
                return
            self._pending.add(key)
        try:
            asyncio.run_coroutine_threadsafe(self._name_once(key), loop)
        except RuntimeError:
            with self._pending_lock:
                self._pending.discard(key)

    async def _name_once(self, key: str) -> None:
        try:
            await self.name(key)
        except Exception:  # noqa: BLE001 - a title is a nicety; it must never surface as a failure
            logger.exception("Could not name session %s", key)
        finally:
            with self._pending_lock:
                self._pending.discard(key)

    async def name(self, key: str) -> str:
        """Write a title for ``key`` if it has none. Returns the title written, or ``""``."""
        if not self.model():
            return ""
        path = archive.find(self._config(), key)
        if path is None:
            return ""

        def read() -> str:
            with TranscriptStore(path) as store:
                meta = store.metadata()
                if meta is None or (meta.title or "").strip():
                    return ""
                return source_text(store)

        text = await asyncio.to_thread(read)
        if not text:
            return ""

        if self._turn is None:
            self._turn = asyncio.Lock()
        async with self._turn:
            try:
                title = await suggest_title(text, self._backend_factory())
            except LlmError as exc:
                logger.info("No title for %s: %s", key, exc.message)
                return ""
        if not title:
            return ""

        def write() -> bool:
            with TranscriptStore(path) as store:
                return store.set_title(title, replace=False)

        if not await asyncio.to_thread(write):
            return ""
        self._emit("session.titled", {"key": key, "title": title})
        return title

    # -- the backfill -----------------------------------------------------------------------

    def untitled(self) -> list[str]:
        """Finished recordings with a transcript worth naming and no title, newest first."""
        return [
            session.key
            for session in archive.list_sessions(self._config())
            if not session.named
            and not session.orphan
            and session.readable
            and session.ended_at
            and session.words >= MIN_WORDS
        ]

    def plan(self) -> dict[str, Any]:
        """What naming the untitled recordings would do. Starts nothing."""
        keys = self.untitled()
        model = self.model()
        return {
            "count": len(keys),
            "model": model,
            "configured": bool(model),
            "backfill": self.backfill.as_dict(),
        }

    def start_backfill(self) -> dict[str, Any]:
        """Name every untitled recording, one at a time, in the background."""
        if self.backfill.running:
            return self.backfill.as_dict()
        keys = self.untitled()
        self.backfill = Backfill(running=bool(keys), total=len(keys))
        if keys:
            self._backfill_task = asyncio.get_running_loop().create_task(self._run_backfill(keys))
        return self.backfill.as_dict()

    async def _run_backfill(self, keys: list[str]) -> None:
        try:
            for key in keys:
                self.backfill.current = key
                try:
                    if await self.name(key):
                        self.backfill.named += 1
                except Exception:  # noqa: BLE001 - one bad file must not end the run
                    logger.exception("Could not name session %s", key)
                self.backfill.done += 1
                self._emit("titles.progress", self.backfill.as_dict())
        finally:
            self.backfill.running = False
            self.backfill.current = ""
            self._emit("titles.done", self.backfill.as_dict())
