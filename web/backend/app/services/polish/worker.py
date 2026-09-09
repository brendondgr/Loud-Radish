"""The background polish loop.

Ticks once a second while a session runs, asks :mod:`.chunker` whether a chunk is ready, and if it
is, sends it to the language model and stores what comes back. Nothing about it requires attention
from the user: there is no button, no progress bar, and no state to manage.

One pass is four steps, and only the second involves the model:

1. :mod:`.source` flattens the chunk into a single continuous run of text with ``[MM:SS]`` markers
   already placed in it. It reads as nonsense at this stage — that is what concatenated
   speech-model output is.
2. The model rewrites that whole run at once. Rewriting the passage entire, rather than fragment by
   fragment, is what lets it repair sentences that were split across three segments.
3. :func:`..guard.collapse_to_paragraph` and :func:`..guard.reconcile_timestamps` enforce the parts
   of the instructions a model complies with unevenly.
4. :func:`..guard.preserves_content` decides whether what came back is a tidied transcript or a
   summary wearing its clothes.

**Nothing here may affect transcription.** The rule is the same one :mod:`..context` operates under
and it is absolute — a language model failure is an inconvenience, a lost transcript is a ruined
seminar. Every call is wrapped, every failure is reported once rather than every second, and the
loop keeps its schedule regardless.

**The fallback is doing nothing.** When the model is missing, unreachable, slow, or returns
something that fails the guard, no polished block is stored and no event is sent. The page then
shows exactly what it shows today: the raw committed segments. That is a deliberate property, not
an accident of error handling — the transcript is the document, and the polish is a reading aid
laid over it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from ...config.schema import AppConfig
from ..llm.contract import GenerationOptions, LlmBackend, system, user
from ..llm.errors import LlmError, LlmServerError
from . import prompts
from .chunker import decide_cut
from .guard import collapse_to_paragraph, preserves_content, reconcile_timestamps, strip_decoration
from .source import build_source

logger = logging.getLogger(__name__)

#: How often the planner is consulted. The pause it is watching for is a couple of seconds long, so
#: a slower tick would routinely miss a break and cut at the ceiling instead.
TICK_INTERVAL_S = 1.0

#: Floor on the output budget for one polish call.
#:
#: A minute of speech is around 150 words, so the answer itself is small. The headroom is for a
#: model that reasons despite being asked not to: on those, the budget is shared between the
#: working and the reply, and a tight cap produces no reply at all rather than a shorter one.
POLISH_TOKENS = 4000

#: Temperature ceiling. This is a transcription task, not a writing one — the less latitude the
#: model has, the less it rewrites.
MAX_TEMPERATURE = 0.2

#: Consecutive failures on the same chunk before it is abandoned and the cursor moves past it.
#: One retry covers a model that was briefly busy; more would mean an unreachable server sending
#: an ever-larger chunk every second for the rest of the talk.
MAX_ATTEMPTS = 2


class PolishWorker:
    """Rewrites the transcript a minute at a time, while the talk is running."""

    def __init__(
        self,
        store: Any,
        config_provider: Callable[[], AppConfig],
        backend_factory: Callable[[], LlmBackend],
        emit: Callable[[str, dict[str, Any]], None],
        silence_provider: Callable[[], float],
    ) -> None:
        self._store = store
        #: Rewrites thrown away for losing too much of what was said. Counted so
        #: `polish.min_retained_ratio` can be tuned from real runs (D-053).
        self._discarded = 0
        #: A provider rather than a snapshot, unlike :class:`..context.ContextWorker`. These
        #: settings are ones a user adjusts while listening — "do this more often", "stop doing
        #: it" — and a snapshot would defer that to the next session.
        self._config = config_provider
        self._backend_factory = backend_factory
        self._emit = emit
        #: Seconds of silence the VAD gate currently reports. Zero while speaking, and permanently
        #: zero when the VAD is disabled, in which case only the chunk ceiling ever fires.
        self._silence = silence_provider

        self._task: asyncio.Task[None] | None = None
        #: How far the transcript has been polished. Seeded from the store so reopening a session
        #: file does not polish the same minutes twice.
        self._cursor = float(store.last_polished_end()) if store is not None else 0.0
        self._attempts = 0
        self._reported_failure = False
        #: Cleared once a server has rejected the no-reasoning fields, so they are sent at most
        #: twice per session rather than on every chunk.
        self._send_reasoning_extras = True

    @property
    def cursor(self) -> float:
        """How far into the talk has been polished."""
        return self._cursor

    def start(self) -> None:
        """Begin the loop. Whether it does anything is re-read from settings on every tick."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="polish-worker")

    async def stop(self) -> None:
        """Stop, and polish whatever is left.

        The tail matters for the same reason it does for summaries: a talk that ends forty seconds
        into a chunk would otherwise leave its closing section — usually the conclusions — as the
        only raw stretch on an otherwise polished page.
        """
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.poll(final=True)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(TICK_INTERVAL_S)
            try:
                await self.poll()
            except Exception:  # noqa: BLE001 - the loop outlives any one failed pass
                logger.exception("Polish pass failed")

    async def poll(self, final: bool = False) -> bool:
        """Run one pass. Returns whether a polished block was produced."""
        config = self._config()
        if not config.polish.enabled:
            return False

        decision = decide_cut(
            cursor=self._cursor,
            last_committed_end=self._store.last_segment_end(),
            silence_seconds=self._silence(),
            config=config.polish,
            final=final,
        )
        if decision.cut_at is None:
            return False

        start, end = self._cursor, decision.cut_at
        segments = self._store.segments_in_range(start, end)
        # Step one: the chunk as a single continuous run with its timestamps already in place. It
        # reads as nonsense at this point, which is what concatenated speech-model output is; the
        # model's job is to rewrite the whole of it at once rather than line by line.
        source = build_source(segments, config.polish.timestamp_interval_s)
        if source.is_empty:
            self._cursor = end
            return False

        logger.debug("Polishing %.1f–%.1f s (%s)", start, end, decision.reason)
        answer = await self._polish(source.text, config)
        if answer is None:
            self._give_up_or_retry(end)
            return False

        # Each guard enforces one of the instructions after the fact, because models comply with
        # them unevenly. Each is also a switch, because an instruction somebody wrote themselves
        # is exactly the thing these would quietly reverse — a rewritten rule 7 asking for
        # paragraphs produces none at all while `collapse_paragraphs` is on, and nothing on the
        # page explains why (D-068). Turning one off does not turn off the fallback below.
        polished = answer
        if config.polish.collapse_paragraphs:
            polished = collapse_to_paragraph(polished)
        if config.polish.reconcile_timestamps:
            polished = reconcile_timestamps(polished, source.labels, fallback=source.first_label)

        check = preserves_content(
            source.text,
            polished,
            config.polish.min_retained_ratio,
            config.polish.max_expansion_ratio,
        )
        if not check.ok:
            # Not retried: a model that summarised when told to tidy will do it again, and a
            # second call costs the same as the first. The raw segments stay on the page, which
            # is the correct outcome — worse-looking, still true.
            # **The ratio, not just the reason.** `ContentCheck.ratio` says in its own docstring
            # that it is "logged, so a threshold can be tuned from real runs" — and this caller
            # dropped it, which is why `polish.min_retained_ratio` has sat at its guessed 0.6 with
            # nothing to tune it against. The floor is printed alongside so the two can be compared
            # without going to look the setting up.
            self._discarded += 1
            logger.info(
                "Discarded a rewrite of %.1f–%.1f s: %s (kept %.2f of the words, floor %.2f; "
                "%d discarded so far)",
                start,
                end,
                check.reason,
                check.ratio,
                config.polish.min_retained_ratio,
                self._discarded,
            )
            self._advance(end)
            return False

        block = self._store.add_polished_block(
            start, end, polished, [segment.id for segment in segments]
        )
        self._advance(end)
        self._emit("transcript.polished", block.as_event())
        return True

    # -- the model call --------------------------------------------------------------

    async def _polish(self, source: str, config: AppConfig) -> str | None:
        """Return the cleaned text, or ``None`` if the model could not produce one."""
        try:
            backend = self._backend_factory()
        except Exception as exc:  # noqa: BLE001 - an unconfigured provider is not a crash
            self._report(str(exc))
            return None

        try:
            answer, finish_reason = await self._generate(backend, source, config)
        except LlmServerError as exc:
            # The most likely cause of a server error on this particular request is the extra
            # no-reasoning fields, which not every server tolerates. One retry without them
            # distinguishes "this server is strict" from "this server is broken".
            if not self._send_reasoning_extras:
                self._report(exc.message)
                return None
            logger.info("Retrying without the no-reasoning fields: %s", exc.message)
            self._send_reasoning_extras = False
            try:
                answer, finish_reason = await self._generate(backend, source, config)
            except LlmError as retry_exc:
                self._report(retry_exc.message)
                return None
        except LlmError as exc:
            self._report(exc.message)
            return None
        except Exception:  # noqa: BLE001 - nothing here may reach the pipeline
            logger.exception("Polish request failed")
            return None

        if not answer:
            if finish_reason == "length":
                self._report(
                    "The assistant used its entire output budget before writing anything. "
                    'Raise "Longest answer" in Settings → Assistant, or turn off its reasoning.'
                )
            return None

        self._reported_failure = False
        return strip_decoration(answer) if config.polish.strip_decoration else answer

    async def _generate(
        self, backend: LlmBackend, source: str, config: AppConfig
    ) -> tuple[str, str]:
        """Run one call and return ``(answer, finish_reason)``.

        Streamed rather than using :meth:`LlmBackend.complete` because the finish reason separates
        "the model had nothing to say" from "the model ran out of room before saying it", and those
        have different remedies. Reasoning chunks are dropped here — a model's working is not the
        transcript.
        """
        extra = (
            dict(prompts.NO_REASONING_EXTRAS)
            if config.polish.disable_reasoning and self._send_reasoning_extras
            else {}
        )
        options = GenerationOptions(
            temperature=min(config.llm.generation.temperature, MAX_TEMPERATURE),
            max_output_tokens=max(POLISH_TOKENS, config.llm.generation.max_output_tokens),
            extra=extra,
        )

        parts: list[str] = []
        finish_reason = ""
        messages = [system(prompts.resolve(config)), user(source)]
        async for chunk in backend.stream(messages, options):
            if chunk.done:
                finish_reason = chunk.finish_reason
            elif chunk.text:
                parts.append(chunk.text)
        return "".join(parts).strip(), finish_reason

    # -- bookkeeping -----------------------------------------------------------------

    def _advance(self, end: float) -> None:
        self._cursor = end
        self._attempts = 0

    def _give_up_or_retry(self, end: float) -> None:
        """Leave the chunk for one more attempt, then move past it.

        Moving past a chunk that cannot be polished is what keeps a dead model from producing an
        ever-growing request every second. The cost is that the stretch stays raw for the rest of
        the session — which is the documented fallback, not a new failure.
        """
        self._attempts += 1
        if self._attempts >= MAX_ATTEMPTS:
            logger.info(
                "Leaving %.1f–%.1f s unpolished after %d attempts",
                self._cursor,
                end,
                self._attempts,
            )
            self._advance(end)

    def _report(self, message: str) -> None:
        """Surface a failure once, saying plainly that the transcript itself is fine."""
        if self._reported_failure:
            return
        self._reported_failure = True
        self._emit(
            "error",
            {
                "code": "polish-unavailable",
                "message": (
                    f"{message} Transcription is unaffected — the transcript is simply shown "
                    f"as the speech model produced it, without the clean-up pass."
                ),
                "severity": "warning",
                "opens_settings": "llm",
                "remedy_label": "Open assistant settings",
                "transcription_continues": True,
            },
        )
