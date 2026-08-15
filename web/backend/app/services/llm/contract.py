"""Seam B — the language model interface (BE §10.2).

The second of the two interfaces the architecture rests on. Its purpose is the same as Seam A's: the
assistant's provider is a configuration value, not an architectural commitment. Chat orchestration
never learns whether it is talking to a local server on this machine or a hosted API.

**Two details that are easy to get wrong.**

*Streaming is the primary path, not an optimisation.* A local model answering a question about a
seminar takes tens of seconds. Waiting for the whole answer before showing anything makes the
assistant feel broken, so :meth:`LlmBackend.stream` is the interface and non-streaming completion is
built on top of it, rather than the other way round.

*Reasoning is not the answer.* Reasoning models emit their working separately from their reply, and
a client that concatenates the two shows the user a monologue instead of an answer.
:class:`LlmChunk` keeps them in different fields, and the two are never merged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import ConnectionTest, LlmError, model_missing

LlmRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LlmMessage:
    """One turn of the conversation sent to the provider."""

    role: LlmRole
    content: str

    def as_dict(self) -> dict[str, str]:
        """Wire format shared by the OpenAI-compatible and Anthropic APIs."""
        return {"role": self.role, "content": self.content}


def system(content: str) -> LlmMessage:
    """Build a system message."""
    return LlmMessage(role="system", content=content)


def user(content: str) -> LlmMessage:
    """Build a user message."""
    return LlmMessage(role="user", content=content)


def assistant(content: str) -> LlmMessage:
    """Build an assistant message."""
    return LlmMessage(role="assistant", content=content)


@dataclass(frozen=True)
class LlmUsage:
    """Token accounting for one request, when the provider reports it."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Prompt plus completion."""
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, int]:
        """JSON-safe payload, carried on the ``chat.done`` event."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class LlmChunk:
    """One fragment of a streaming answer.

    ``text`` and ``reasoning`` are deliberately separate. A model that exposes its working emits
    reasoning first and content afterwards, and only ``text`` belongs in the answer the user reads.
    """

    #: A fragment of the answer itself. Appended to what the user sees.
    text: str = ""
    #: A fragment of the model's working, when it exposes it. Shown separately, or not at all.
    reasoning: str = ""
    #: Set on the final chunk only.
    done: bool = False
    #: Present on the final chunk when the provider reported token counts.
    usage: LlmUsage | None = None
    #: Why generation stopped: ``stop``, ``length``, ``cancelled``, or provider-specific.
    finish_reason: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether this chunk carries nothing to display."""
        return not self.text and not self.reasoning


@dataclass(frozen=True)
class LlmCapabilities:
    """What a provider can do, so orchestration never special-cases a model name."""

    #: Server-sent streaming. Every provider here supports it; the flag exists for future ones.
    streaming: bool = True
    #: The model exposes its reasoning as a separate field.
    reasoning: bool = False
    #: Tokens the model can be given. Drives the context budget, so a wrong value truncates badly.
    context_window: int = 8192
    #: Whether requests leave this machine. Drives the privacy indicator (FE §7.6).
    local: bool = True

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``GET /api/llm/config``."""
        return {
            "streaming": self.streaming,
            "reasoning": self.reasoning,
            "context_window": self.context_window,
            "local": self.local,
        }


@dataclass(frozen=True)
class LlmModelInfo:
    """One model the configured endpoint reports."""

    id: str
    owned_by: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, str]:
        """JSON-safe payload for ``GET /api/llm/models``."""
        return {"id": self.id, "owned_by": self.owned_by, "note": self.note}


@dataclass(frozen=True)
class GenerationOptions:
    """Per-request generation parameters.

    ``max_output_tokens`` has a subtlety worth stating: on a reasoning model the budget is shared
    between the working and the reply, so a value tuned for a plain model can produce an answer
    that is entirely reasoning and no content. Callers that see an empty answer with
    ``finish_reason == "length"`` should raise this value rather than treat it as a failure.
    """

    temperature: float = 0.3
    max_output_tokens: int = 1024
    #: Extra provider-specific fields merged into the request body. Used sparingly.
    extra: dict[str, Any] = field(default_factory=dict)


class LlmBackend(ABC):
    """A language model provider.

    Constructed cheaply — no network work in ``__init__`` — so the settings interface can describe
    a provider without contacting it.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Stable identifier used in configuration, e.g. ``"openai-compatible"``."""

    @property
    @abstractmethod
    def model_id(self) -> str:
        """The model this backend will send requests to."""

    @property
    @abstractmethod
    def endpoint(self) -> str:
        """The address requests go to. Shown in error messages, so it must be the real one."""

    @property
    @abstractmethod
    def capabilities(self) -> LlmCapabilities:
        """What this provider can do."""

    @abstractmethod
    async def list_models(self) -> list[LlmModelInfo]:
        """Ask the endpoint what it offers.

        Raises:
            LlmError: with a message naming the remedy.
        """

    @abstractmethod
    def stream(
        self, messages: list[LlmMessage], options: GenerationOptions | None = None
    ) -> AsyncIterator[LlmChunk]:
        """Stream an answer.

        Cancellation is ordinary ``asyncio`` cancellation: closing the iterator, or cancelling the
        task awaiting it, aborts the underlying request. No separate cancel token exists, because a
        second mechanism would only be a way for the two to disagree.

        Raises:
            LlmError: with a message naming the remedy.
        """

    async def test(self) -> ConnectionTest:
        """Probe the provider and classify the outcome into one of four results.

        The default implementation is enough for any provider that can list models: reaching the
        endpoint at all rules out ``no_server`` and ``auth_rejected``, and the configured model
        either appears in the list or does not.
        """
        try:
            models = await self.list_models()
        except LlmError as exc:
            return exc.as_test()

        names = [model.id for model in models]
        if self.model_id and names and self.model_id not in names:
            return model_missing(self.model_id, self.endpoint, names).as_test()

        chosen = self.model_id or "(none selected)"
        return ConnectionTest(
            result="connected",
            message=f"Connected to {self.endpoint}. Using {chosen}.",
            models=names,
        )

    async def complete(
        self, messages: list[LlmMessage], options: GenerationOptions | None = None
    ) -> str:
        """Collect a streamed answer into one string.

        Built on :meth:`stream` rather than a separate non-streaming request, so there is only one
        code path to get right. Reasoning is discarded here — it is working, not answer.
        """
        parts: list[str] = []
        async for chunk in self.stream(messages, options):
            if chunk.text:
                parts.append(chunk.text)
        return "".join(parts).strip()

    def describe(self) -> dict[str, Any]:
        """A JSON-safe description for the settings interface."""
        return {
            "provider": self.provider_id,
            "model": self.model_id,
            "endpoint": self.endpoint,
            "capabilities": self.capabilities.as_dict(),
        }
