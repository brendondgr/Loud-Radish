"""Asking a question, streaming the answer, and stopping it.

The rule these tests exist to hold is the one from BE §15: **nothing here may stop transcription**.
So every failure path is checked for the same two properties — it is reported as a warning rather
than a critical error, and it does not raise into the caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from app.config.defaults import default_config
from app.models.segment import Segment
from app.services.chat import ChatError, ChatRequest, ChatService
from app.services.llm.contract import LlmChunk, LlmUsage
from app.services.llm.errors import LlmUnreachableError
from app.services.transcript import TranscriptStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ScriptedBackend:
    """An LLM that emits exactly what a test tells it to."""

    def __init__(self, chunks=None, error=None, model="default-model", delay=0.0):
        self._chunks = chunks if chunks is not None else [LlmChunk(text="Because it is dense.")]
        self._error = error
        self._model = model
        self._delay = delay
        self.messages = None
        self.closed = False

    provider_id = "local"
    endpoint = "http://localhost:9090/v1"

    @property
    def model_id(self) -> str:
        return self._model

    async def _generate(self) -> AsyncIterator[LlmChunk]:
        if self._error:
            raise self._error
        try:
            for chunk in self._chunks:
                if self._delay:
                    await asyncio.sleep(self._delay)
                yield chunk
            yield LlmChunk(done=True, usage=LlmUsage(10, 5), finish_reason="stop")
        finally:
            self.closed = True

    def stream(self, messages, options=None):
        self.messages = messages
        return self._generate()


class Recorder:
    """Collects emitted events, as the hub would."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def __call__(self, name, payload):
        self.events.append((name, payload))

    def of(self, name):
        return [payload for event, payload in self.events if event == name]

    def text(self):
        return "".join(p.get("text", "") for p in self.of("chat.delta"))


@pytest.fixture
def store(tmp_path):
    with TranscriptStore(tmp_path / "session.db") as store:
        for index in range(6):
            store.append_segment(
                Segment(
                    id=index + 1,
                    text=f"the operators commute on the dense subspace, point {index}",
                    start=index * 10.0,
                    end=index * 10.0 + 8.0,
                )
            )
        yield store


def build(store, backend, clock=60.0, config=None):
    recorder = Recorder()
    service = ChatService(
        store_provider=lambda: store,
        config_provider=lambda: config or default_config(),
        backend_factory=lambda: backend,
        emit=recorder,
        clock=lambda: clock,
    )
    return service, recorder


async def settle(service: ChatService) -> None:
    """Wait for the in-flight answer to finish."""
    if service._task is not None:
        await asyncio.gather(service._task, return_exceptions=True)


# -- the happy path -------------------------------------------------------------------


async def test_an_answer_streams_as_deltas_and_ends_with_done(store) -> None:
    backend = ScriptedBackend(chunks=[LlmChunk(text="Because "), LlmChunk(text="it is dense.")])
    service, events = build(store, backend)

    accepted = await service.ask(ChatRequest(message="why do they commute?"))
    await settle(service)

    assert accepted["request_id"]
    assert events.text() == "Because it is dense."
    done = events.of("chat.done")[0]
    assert done["request_id"] == accepted["request_id"]
    assert done["usage"]["total_tokens"] == 15


async def test_reasoning_never_arrives_as_answer_text(store) -> None:
    """Merging the two shows the user a monologue instead of an answer."""
    backend = ScriptedBackend(chunks=[LlmChunk(reasoning="Let me think."), LlmChunk(text="Yes.")])
    service, events = build(store, backend)

    await service.ask(ChatRequest(message="is it dense?"))
    await settle(service)

    assert events.text() == "Yes."
    assert any(p.get("reasoning") for p in events.of("chat.delta"))


async def test_both_turns_are_persisted_so_a_reload_keeps_the_conversation(store) -> None:
    service, _ = build(store, ScriptedBackend())

    await service.ask(ChatRequest(message="why?"))
    await settle(service)

    roles = [message["role"] for message in service.history()]
    assert roles == ["user", "assistant"]


async def test_a_quick_action_supplies_its_own_prompt_and_window(store) -> None:
    backend = ScriptedBackend()
    service, _ = build(store, backend, clock=60.0)

    await service.ask(ChatRequest(action="summarise_10"))
    await settle(service)

    sent = "\n".join(message.content for message in backend.messages)
    assert "last ten minutes" in sent


async def test_a_selection_is_quoted_into_the_question(store) -> None:
    backend = ScriptedBackend()
    service, _ = build(store, backend)

    await service.ask(
        ChatRequest(message="what does this mean?", quote="deficiency indices", quote_start=12.0)
    )
    await settle(service)

    asked = backend.messages[-1].content
    assert "deficiency indices" in asked
    assert "what does this mean?" in asked


async def test_what_did_i_miss_starts_from_where_the_last_answer_left_off(store) -> None:
    backend = ScriptedBackend()
    service, _ = build(store, backend, clock=60.0)

    await service.ask(ChatRequest(message="first question"))
    await settle(service)
    assert service.mark_read() >= 0

    await service.ask(ChatRequest(action="what_missed"))
    await settle(service)

    sent = "\n".join(message.content for message in backend.messages)
    assert "exactly the stretch the user has not read" in sent


# -- refusals -------------------------------------------------------------------------


async def test_asking_before_anything_is_recorded_names_the_remedy() -> None:
    service = ChatService(
        store_provider=lambda: None,
        config_provider=default_config,
        backend_factory=lambda: ScriptedBackend(),
        emit=Recorder(),
        clock=lambda: 0.0,
    )

    with pytest.raises(ChatError, match="Start recording"):
        await service.ask(ChatRequest(message="why?"))


async def test_asking_with_no_model_selected_names_the_setting(store) -> None:
    service, _ = build(store, ScriptedBackend(model=""))

    with pytest.raises(ChatError, match="Settings"):
        await service.ask(ChatRequest(message="why?"))


async def test_a_second_question_while_one_is_in_flight_is_refused(store) -> None:
    backend = ScriptedBackend(chunks=[LlmChunk(text="slow")], delay=0.2)
    service, _ = build(store, backend)

    await service.ask(ChatRequest(message="first"))
    with pytest.raises(ChatError, match="still being written"):
        await service.ask(ChatRequest(message="second"))

    await service.cancel()


async def test_an_empty_question_is_refused_before_any_request_is_made(store) -> None:
    service, _ = build(store, ScriptedBackend())

    with pytest.raises(ChatError, match="Ask a question"):
        await service.ask(ChatRequest(message="   "))


# -- failures are never fatal -----------------------------------------------------------


async def test_an_unreachable_model_is_a_warning_not_a_critical_error(store) -> None:
    """Critical is reserved for what stops transcription. This does not."""
    backend = ScriptedBackend(error=LlmUnreachableError("No server responded at :9090."))
    service, events = build(store, backend)

    await service.ask(ChatRequest(message="why?"))
    await settle(service)

    error = events.of("error")[0]
    assert error["severity"] == "warning"
    assert error["transcription_continues"] is True
    assert error["opens_settings"] == "llm"
    # The stream must still be closed out, or the interface waits forever.
    assert events.of("chat.done")[0]["finish_reason"] == "error"


async def test_an_unexpected_failure_does_not_escape_into_the_session(store) -> None:
    backend = ScriptedBackend(error=ValueError("something odd"))
    service, events = build(store, backend)

    await service.ask(ChatRequest(message="why?"))
    await settle(service)

    assert "transcript is safe" in events.of("error")[0]["message"]


async def test_an_all_reasoning_answer_names_the_setting_that_fixes_it(store) -> None:
    """Reporting it as an empty answer sends the user looking for a bug that is not there."""
    backend = ScriptedBackend(
        chunks=[LlmChunk(reasoning="thinking" * 50), LlmChunk(done=True, finish_reason="length")]
    )
    service, events = build(store, backend)

    await service.ask(ChatRequest(message="why?"))
    await settle(service)

    assert "Longest answer" in events.of("error")[0]["message"]


# -- cancellation ----------------------------------------------------------------------


async def test_cancelling_stops_the_stream_and_keeps_what_arrived(store) -> None:
    """The user read the part that arrived; losing it makes the history disagree with the screen."""
    backend = ScriptedBackend(
        chunks=[LlmChunk(text="Because "), LlmChunk(text="never sent")], delay=0.15
    )
    service, events = build(store, backend)

    await service.ask(ChatRequest(message="why?"))
    await asyncio.sleep(0.2)
    assert await service.cancel() is True

    assert events.of("chat.done")[0]["finish_reason"] == "cancelled"
    assert [m["text"] for m in service.history() if m["role"] == "assistant"] == ["Because"]
    assert backend.closed, "the underlying request must be closed, not left hanging"


async def test_cancelling_nothing_is_not_an_error(store) -> None:
    service, _ = build(store, ScriptedBackend())
    assert await service.cancel() is False


async def test_clearing_the_conversation_leaves_the_transcript_alone(store) -> None:
    service, _ = build(store, ScriptedBackend())

    await service.ask(ChatRequest(message="why?"))
    await settle(service)
    service.clear_history()

    assert service.history() == []
    assert len(store.all_segments()) == 6
