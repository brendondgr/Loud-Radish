"""Chat orchestration — one question in, a streamed answer out (BE §11).

The whole of this module rests on one rule, and it is worth stating before anything else:

    **Transcription is the critical path.** Nothing here may stop it. Every failure below is caught
    and reported as a chat error, because a chat error is an inconvenience and a lost transcript is
    a ruined seminar.

Answers stream. A local model answering a question about a seminar takes tens of seconds, and a
button that does nothing for half a minute is indistinguishable from a broken one. Deltas go out
over the same WebSocket as everything else, so the interface has one event stream rather than two
transports to keep in step.

**Cancellation is ordinary task cancellation.** The stop button cancels the task, which closes the
generator, which aborts the HTTP request. There is no separate cancel flag, because a second
mechanism is only ever a way for the two to disagree.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...config.schema import AppConfig, QuickAction
from ..context.assembler import ContextRequest, assemble, resolve_citations
from ..context.prompts import quick_action_prompt, selection_prompt
from ..llm.contract import GenerationOptions, LlmBackend
from ..llm.errors import LlmError

logger = logging.getLogger(__name__)


class ChatError(RuntimeError):
    """A request that cannot be started. The message names the remedy."""


@dataclass
class ChatRequest:
    """One question, however it was asked."""

    message: str = ""
    action: str | None = None
    quote: str | None = None
    quote_start: float | None = None


class ChatService:
    """Runs one question at a time against the configured language model."""

    def __init__(
        self,
        store_provider: Callable[[], Any | None],
        config_provider: Callable[[], AppConfig],
        backend_factory: Callable[[], LlmBackend],
        emit: Callable[[str, dict[str, Any]], None],
        clock: Callable[[], float],
    ) -> None:
        self._store_provider = store_provider
        self._config_provider = config_provider
        self._backend_factory = backend_factory
        self._emit = emit
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._request_id = ""
        #: Where the user had read up to when they last asked "what did I miss". Held here rather
        #: than in the browser so it survives a reload mid-talk.
        self._read_mark = 0.0

    @property
    def is_busy(self) -> bool:
        """Whether an answer is currently being produced."""
        return self._task is not None and not self._task.done()

    @property
    def request_id(self) -> str:
        """The in-flight request's id, or an empty string."""
        return self._request_id if self.is_busy else ""

    def quick_actions(self) -> list[QuickAction]:
        """The configured one-tap prompts."""
        return list(self._config_provider().quick_actions)

    def history(self, limit: int | None = None) -> list[dict[str, Any]]:
        """The conversation so far."""
        store = self._store_provider()
        return [message.as_dict() for message in store.chat_history(limit)] if store else []

    def clear_history(self) -> None:
        """Delete the conversation. The transcript is untouched."""
        store = self._store_provider()
        if store:
            store.clear_chat_history()

    def mark_read(self, position: float | None = None) -> float:
        """Record how far the user has read, for the "what did I miss" action."""
        self._read_mark = self._clock() if position is None else max(0.0, position)
        return self._read_mark

    # -- asking ----------------------------------------------------------------------

    async def ask(self, request: ChatRequest) -> dict[str, Any]:
        """Start answering. Returns immediately; the answer arrives as ``chat.delta`` events.

        Raises:
            ChatError: when there is nothing to answer from, no model configured, or a question is
                already in flight.
        """
        store = self._store_provider()
        if store is None:
            raise ChatError(
                "There is no transcript to ask about yet. Start recording, then ask again."
            )
        if self.is_busy:
            raise ChatError("An answer is still being written. Stop it before asking again.")

        config = self._config_provider()
        question, since = self._resolve(request, config)
        if not question.strip():
            raise ChatError("Ask a question, or choose one of the suggested ones.")

        try:
            backend = self._backend_factory()
        except LlmError as exc:
            raise ChatError(exc.message) from exc

        if not backend.model_id:
            raise ChatError("No language model is selected. Choose one in Settings → Assistant.")

        context = assemble(
            store,
            config,
            ContextRequest(
                question=question,
                now=self._clock(),
                quote=request.quote or "",
                quote_start=request.quote_start,
                range_minutes=self._range_minutes(request, config),
                since=since,
            ),
        )

        self._request_id = uuid.uuid4().hex[:12]
        store.add_chat_message("user", question, context_timestamp=context.context_timestamp)

        self._task = asyncio.create_task(
            self._stream(backend, config, context, store, self._request_id),
            name=f"chat-{self._request_id}",
        )
        return {"request_id": self._request_id, "context_timestamp": context.context_timestamp}

    async def cancel(self) -> bool:
        """Stop the in-flight answer. Returns whether there was one."""
        task, self._task = self._task, None
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    async def shutdown(self) -> None:
        """Cancel anything in flight, for session teardown."""
        await self.cancel()

    # -- the stream ------------------------------------------------------------------

    async def _stream(
        self,
        backend: LlmBackend,
        config: AppConfig,
        context: Any,
        store: Any,
        request_id: str,
    ) -> None:
        options = GenerationOptions(
            temperature=config.llm.generation.temperature,
            max_output_tokens=config.llm.generation.max_output_tokens,
        )
        parts: list[str] = []
        usage: Any = None
        finish_reason = ""
        stream = backend.stream(context.messages, options)

        try:
            async for chunk in stream:
                if chunk.done:
                    usage, finish_reason = chunk.usage, chunk.finish_reason
                    break
                if chunk.text:
                    parts.append(chunk.text)
                    self._emit("chat.delta", {"request_id": request_id, "text": chunk.text})
                elif chunk.reasoning:
                    # Sent under its own key so the interface can show "thinking…" without ever
                    # mistaking the model's working for its answer.
                    self._emit(
                        "chat.delta", {"request_id": request_id, "reasoning": chunk.reasoning}
                    )

        except asyncio.CancelledError:
            await self._finish(store, request_id, "".join(parts), context, None, "cancelled")
            raise
        except LlmError as exc:
            self._fail(request_id, exc.message)
            return
        except Exception:  # noqa: BLE001 - an unexpected shape must not take the session with it
            logger.exception("Chat request %s failed", request_id)
            self._fail(request_id, "The assistant failed unexpectedly. The transcript is safe.")
            return
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

        answer = "".join(parts).strip()

        # **Checked against the transcript before it is kept.** Stored timestamps were measured and
        # found correct at both revisions (`tests/transcription/test_timestamp_alignment.py`), so a
        # cited moment matching no line the model was shown was invented — interpolated between two
        # lines, or lifted from the summary block, whose ranges are not moments. The reader clicks
        # it, lands somewhere unrelated, and stops trusting the citations that *are* right, which is
        # the whole complaint. The sentence is kept; only the number goes.
        answer, dropped = resolve_citations(answer, set(context.offered_seconds))
        if dropped:
            logger.info(
                "Answer %s cited %d moment(s) that are in no transcript line: %s",
                request_id,
                len(dropped),
                dropped,
            )

        if not answer and finish_reason == "length":
            # A reasoning model can spend its whole budget thinking. Reporting that as an empty
            # answer sends the user looking for a bug; naming the setting fixes it in one step.
            self._fail(
                request_id,
                "The model used its entire output budget before answering. Raise "
                '"Longest answer" in Settings → Assistant.',
            )
            return

        await self._finish(store, request_id, answer, context, usage, finish_reason)

    async def _finish(
        self,
        store: Any,
        request_id: str,
        answer: str,
        context: Any,
        usage: Any,
        finish_reason: str,
    ) -> None:
        """Persist the answer and close the stream.

        A cancelled answer is still saved. The user read the part that arrived, and losing it on
        cancel means the conversation history disagrees with what is on screen.
        """
        # Stripped here rather than at each call site: a cancelled answer is cut mid-stream and
        # usually ends on the space between two words.
        answer = answer.strip()
        if answer:
            with contextlib.suppress(Exception):
                store.add_chat_message(
                    "assistant",
                    answer,
                    context_timestamp=context.context_timestamp,
                    meta={"cites": context.cites, "finish_reason": finish_reason},
                )

        self._emit(
            "chat.done",
            {
                "request_id": request_id,
                "usage": usage.as_dict() if usage else {},
                "finish_reason": finish_reason,
                **context.as_dict(),
            },
        )
        self.mark_read(context.context_timestamp)

    def _fail(self, request_id: str, message: str) -> None:
        """Report a chat failure — always a warning, never critical.

        Critical is reserved for things that stop transcription. The assistant not answering does
        not, and dressing it as critical trains the user to ignore the tier that matters.
        """
        self._emit("chat.done", {"request_id": request_id, "usage": {}, "finish_reason": "error"})
        self._emit(
            "error",
            {
                "code": "chat-failed",
                "message": message,
                "severity": "warning",
                "opens_settings": "llm",
                "remedy_label": "Open assistant settings",
                "transcription_continues": True,
            },
        )

    # -- request shaping --------------------------------------------------------------

    def _resolve(self, request: ChatRequest, config: AppConfig) -> tuple[str, float | None]:
        """Turn a typed question, a quick action, or a selection into one prompt."""
        action = self._action(request.action, config)

        if action is not None:
            prompt = quick_action_prompt(action.prompt, action.since_last_read)
            since = self._read_mark if action.since_last_read else None
        else:
            prompt = request.message
            since = None

        if request.quote:
            prompt = selection_prompt(request.quote, prompt)

        return prompt, since

    def _range_minutes(self, request: ChatRequest, config: AppConfig) -> float | None:
        action = self._action(request.action, config)
        return action.range_minutes if action else None

    def _action(self, action_id: str | None, config: AppConfig) -> QuickAction | None:
        if not action_id:
            return None
        return next((a for a in config.quick_actions if a.id == action_id), None)
