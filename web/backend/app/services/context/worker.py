"""Rolling summaries and glossary extraction, produced while the talk is running (BE §9.2, §9.3).

Summarisation is what makes a two-hour talk answerable at all: the earliest hour cannot be sent
verbatim, so it is sent compressed. Doing it as the talk proceeds rather than on demand means the
cost is spread across the session instead of landing on the first question asked at the two-hour
mark.

**Nothing here may affect transcription.** The rule from BE §15 is absolute — an LLM failure is an
inconvenience, a lost transcript is a ruined seminar. So every call is wrapped, every failure is
logged and reported once rather than repeatedly, and the worker keeps its schedule regardless. A
summary that could not be produced is a gap in the outline, not a stopped pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from typing import Any

from ...config.schema import AppConfig
from ..llm.contract import GenerationOptions, LlmBackend, system, user
from ..llm.errors import LlmError
from . import prompts
from .assembler import timestamp

logger = logging.getLogger(__name__)

#: Don't summarise a stretch shorter than this — there is nothing to compress, and a summary of two
#: sentences is longer than the sentences.
MIN_SUMMARY_WORDS = 60

#: The floor on the final pass, which exists to catch a talk's closing section. Lower than
#: :data:`MIN_SUMMARY_WORDS` because a conclusion is worth summarising even when it is short — but
#: not zero: asked to summarise a dozen words a model returns nothing at all, which then looks
#: exactly like a failure.
MIN_FINAL_SUMMARY_WORDS = 25

#: Floor on the output budget for one summarisation call.
#:
#: A summary is three or four sentences — perhaps 150 tokens. This is an order of magnitude more,
#: and deliberately so: on a reasoning model the budget is shared between the working and the reply,
#: and the working on a short passage runs to thousands of tokens. Measured against a live server,
#: a 400-token budget produced *nothing but reasoning* every time, and 2500 still did on one run of
#: two. A tight cap here does not produce a shorter summary; it produces no summary at all.
#:
#: The user's own ``max_output_tokens`` is used when it is larger, since that is the value they
#: tuned for their model.
SUMMARY_TOKENS = 4000
GLOSSARY_TOKENS = 4000

#: ``TERM :: definition``, the format the glossary prompt asks for.
_TERM_LINE = re.compile(r"^\s*(?P<term>[^:]{2,60}?)\s*::\s*(?P<definition>.+?)\s*$")


class ContextWorker:
    """Produces summaries and glossary terms on a schedule while a session runs."""

    def __init__(
        self,
        store: Any,
        config: AppConfig,
        backend_factory: Callable[[], LlmBackend],
        emit: Callable[[str, dict[str, Any]], None],
        clock: Callable[[], float],
    ) -> None:
        self._store = store
        self._config = config
        self._backend_factory = backend_factory
        self._emit = emit
        #: Session-relative seconds. Supplied rather than read from a wall clock so a test can
        #: drive a two-hour talk in a second.
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._cursor = 0.0
        #: Set once a failure has been reported, so a model that is simply not running does not
        #: produce a banner every interval for two hours.
        self._reported_failure = False

    @property
    def cursor(self) -> float:
        """How far into the talk has been summarised."""
        return self._cursor

    def start(self) -> None:
        """Begin the schedule. Does nothing if summaries are disabled."""
        if not self._config.context.summary_enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="context-worker")

    async def stop(self) -> None:
        """Stop, and summarise whatever is left.

        The tail matters: a talk that ends four minutes into a five-minute interval would otherwise
        have its closing section — usually the conclusions — missing from the outline entirely.
        """
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.summarise_pending(final=True)

    async def _run(self) -> None:
        interval = self._config.context.summary_interval_s
        while True:
            await asyncio.sleep(interval)
            await self.summarise_pending()

    async def summarise_pending(self, final: bool = False) -> None:
        """Summarise everything since the last summary, if there is enough of it."""
        now = self._clock()
        segments = self._store.segments_in_range(self._cursor, now)
        text = " ".join(segment.text for segment in segments).strip()

        # On the final pass a short tail is still summarised: it is the end of the talk, and
        # "too short to bother with" is not true of a conclusion.
        floor = MIN_FINAL_SUMMARY_WORDS if final else MIN_SUMMARY_WORDS
        if not text or len(text.split()) < floor:
            return

        start, end = self._cursor, now
        try:
            backend = self._backend_factory()
            summary, finish_reason = await self._generate(
                backend, prompts.SUMMARY_PROMPT, text, SUMMARY_TOKENS
            )
        except LlmError as exc:
            self._report(exc.message)
            return
        except Exception:  # noqa: BLE001 - nothing here may reach the pipeline
            logger.exception("Summarisation failed")
            return

        if not summary and finish_reason == "length":
            # The model spent its whole budget thinking. Left silent this produces an outline with
            # unexplained gaps, so it is reported — and the cursor stays put, so raising the setting
            # recovers the missing stretch rather than losing it permanently.
            self._report(
                "The assistant used its entire output budget before writing a summary. "
                'Raise "Longest answer" in Settings → Assistant.'
            )
            return

        # The cursor advances only on success, so a failed interval is retried with the next one
        # rather than leaving a permanent hole in the outline.
        self._cursor = end
        self._reported_failure = False

        if summary:
            record = self._store.add_summary(start, end, summary)
            self._emit(
                "summary.added",
                {"id": record.id, "start": start, "end": end, "text": summary},
            )
        else:
            # An empty answer that did not run out of room: the model had nothing to say about this
            # stretch. Logged rather than shown — a banner for "there was nothing to summarise" is
            # noise, but silence with no trace at all is impossible to diagnose.
            logger.info(
                "No summary produced for %.1f–%.1f s (finish reason: %s)",
                start,
                end,
                finish_reason or "none",
            )

        if self._config.context.glossary_enabled:
            await self._extract_terms(text, start)

    async def _extract_terms(self, text: str, first_seen: float) -> None:
        """Pull new terms out of the same window that was just summarised."""
        try:
            backend = self._backend_factory()
            raw, _ = await self._generate(backend, prompts.GLOSSARY_PROMPT, text, GLOSSARY_TOKENS)
        except Exception:  # noqa: BLE001 - never fatal, and a missing term is not worth a banner
            logger.debug("Glossary extraction failed", exc_info=True)
            return

        known = {term.term.casefold() for term in self._store.glossary()}
        for term, definition in parse_terms(raw):
            if term.casefold() in known:
                continue
            record = self._store.add_glossary_term(term, definition, first_seen)
            self._emit(
                "glossary.added",
                {
                    "term": record.term,
                    "definition": record.definition,
                    "first_seen": first_seen,
                    "first_seen_label": timestamp(first_seen),
                },
            )

    async def _generate(
        self, backend: LlmBackend, instruction: str, text: str, floor_tokens: int
    ) -> tuple[str, str]:
        """Run one call and return ``(answer, finish_reason)``.

        Streamed rather than using :meth:`LlmBackend.complete` because the finish reason is the
        difference between "the model had nothing to say" and "the model ran out of room before it
        said it" — and those have completely different remedies.
        """
        options = GenerationOptions(
            temperature=min(self._config.llm.generation.temperature, 0.3),
            max_output_tokens=max(floor_tokens, self._config.llm.generation.max_output_tokens),
        )
        parts: list[str] = []
        finish_reason = ""
        async for chunk in backend.stream([system(instruction), user(text)], options):
            if chunk.done:
                finish_reason = chunk.finish_reason
            elif chunk.text:
                parts.append(chunk.text)
        return "".join(parts).strip(), finish_reason

    def _report(self, message: str) -> None:
        """Surface a language-model failure once, as a warning that names transcription is fine."""
        if self._reported_failure:
            return
        self._reported_failure = True
        self._emit(
            "error",
            {
                "code": "summary-unavailable",
                "message": (
                    f"{message} Transcription is unaffected — only the running summary and "
                    f"glossary are paused."
                ),
                "severity": "warning",
                "opens_settings": "llm",
                "remedy_label": "Open assistant settings",
                "transcription_continues": True,
            },
        )


def parse_terms(raw: str) -> list[tuple[str, str]]:
    """Read ``TERM :: definition`` lines, ignoring anything else the model wrote.

    Models add a preamble roughly one time in ten however firmly they are told not to. Parsing by
    line shape rather than by position means "Here are the terms:" costs nothing.
    """
    terms: list[tuple[str, str]] = []
    for line in (raw or "").splitlines():
        match = _TERM_LINE.match(line)
        if not match:
            continue
        term = match.group("term").strip().strip("*-•· ")
        definition = match.group("definition").strip()
        if term and definition and len(term) <= 60:
            terms.append((term, definition))
    return terms
