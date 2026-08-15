"""The OpenAI-compatible client, driven by a stub transport rather than a live server.

Every test here is about a *shape* the client must survive: a stream that interleaves reasoning with
content, a server that omits usage, a keep-alive comment in the middle of an SSE stream, an error
status arriving instead of a stream. Those are the cases a live server exercises only occasionally
and never on demand.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.services.llm.contract import GenerationOptions, user
from app.services.llm.errors import LlmAuthError, LlmServerError, LlmUnreachableError
from app.services.llm.openai_compatible import OpenAiCompatibleBackend

pytestmark = pytest.mark.anyio

ENDPOINT = "http://localhost:9090/v1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def sse(*frames: dict | str) -> bytes:
    """Render frames as an SSE body, exactly as a server would."""
    lines = []
    for frame in frames:
        payload = frame if isinstance(frame, str) else json.dumps(frame)
        lines.append(f"data: {payload}\n\n")
    return "".join(lines).encode()


def delta(**fields) -> dict:
    """One ``chat.completion.chunk`` carrying a delta."""
    return {"choices": [{"index": 0, "delta": fields, "finish_reason": None}]}


def backend(handler, **kwargs) -> OpenAiCompatibleBackend:
    """Build a client whose transport is a callable, so no socket is involved."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAiCompatibleBackend(
        endpoint=ENDPOINT, model="default-model", client=client, **kwargs
    )


async def collect(stream) -> list:
    return [chunk async for chunk in stream]


# -- streaming -------------------------------------------------------------------------


async def test_content_and_reasoning_stay_in_separate_fields() -> None:
    """The whole point of the chunk shape: working must never leak into the answer."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                delta(role="assistant", content=""),
                delta(reasoning="The user asked "),
                delta(reasoning="a question."),
                delta(content="Hello"),
                delta(content=" there."),
                "[DONE]",
            ),
        )

    chunks = await collect(backend(handler).stream([user("hi")]))

    assert "".join(chunk.text for chunk in chunks) == "Hello there."
    assert "".join(chunk.reasoning for chunk in chunks) == "The user asked a question."


async def test_reasoning_content_is_recognised_under_its_other_name() -> None:
    """Servers disagree on the field name; both must land in ``reasoning``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=sse(delta(reasoning_content="thinking"), "[DONE]"))

    chunks = await collect(backend(handler).stream([user("hi")]))
    assert "".join(chunk.reasoning for chunk in chunks) == "thinking"
    assert "".join(chunk.text for chunk in chunks) == ""


async def test_an_all_reasoning_answer_is_reported_rather_than_looking_empty() -> None:
    """A reasoning model can spend the whole budget thinking.

    That is not a transport failure and must not be raised as one — the final chunk says ``length``
    so the caller can tell "the model needs more room" from "the server broke".
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                delta(reasoning="Let me work through this"),
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]},
                "[DONE]",
            ),
        )

    chunks = await collect(backend(handler).stream([user("hi")]))
    final = chunks[-1]

    assert final.done is True
    assert final.finish_reason == "length"
    assert "".join(chunk.text for chunk in chunks) == ""


async def test_keep_alives_and_unparseable_frames_do_not_break_a_working_stream() -> None:
    """One bad frame must not lose an answer that is otherwise arriving fine."""

    body = (
        b": keep-alive\n\n"
        b"event: ping\n\n"
        + sse(delta(content="one"))
        + b"data: {not json}\n\n"
        + sse(delta(content=" two"), "[DONE]")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    chunks = await collect(backend(handler).stream([user("hi")]))
    assert "".join(chunk.text for chunk in chunks) == "one two"


async def test_usage_is_carried_on_the_final_chunk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                delta(content="hi"),
                {"choices": [], "usage": {"prompt_tokens": 21, "completion_tokens": 4}},
                "[DONE]",
            ),
        )

    chunks = await collect(backend(handler).stream([user("hi")]))

    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 25


async def test_generation_options_reach_the_request_body() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=sse("[DONE]"))

    options = GenerationOptions(temperature=0.9, max_output_tokens=256)
    await collect(backend(handler).stream([user("hi")], options))

    assert seen["temperature"] == 0.9
    assert seen["max_tokens"] == 256
    assert seen["stream"] is True
    assert seen["model"] == "default-model"


async def test_complete_returns_only_the_answer() -> None:
    """``complete`` discards reasoning: it is working, not answer."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=sse(delta(reasoning="hmm"), delta(content="  Yes.  "), "[DONE]")
        )

    assert await backend(handler).complete([user("hi")]) == "Yes."


# -- failure taxonomy ------------------------------------------------------------------


async def test_nothing_listening_is_no_server_and_names_the_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LlmUnreachableError) as caught:
        await backend(handler).list_models()

    assert caught.value.result == "no_server"
    assert ENDPOINT in caught.value.message


async def test_a_rejected_key_is_auth_rejected_not_a_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    with pytest.raises(LlmAuthError) as caught:
        await backend(handler, api_key="wrong").list_models()

    assert caught.value.result == "auth_rejected"


async def test_a_five_hundred_quotes_the_server_but_stays_a_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream model crashed")

    with pytest.raises(LlmServerError) as caught:
        await backend(handler).list_models()

    assert caught.value.result == "server_error"
    assert "upstream model crashed" in caught.value.message


async def test_an_error_status_on_the_stream_is_raised_rather_than_yielded_as_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model is loading")

    with pytest.raises(LlmServerError):
        await collect(backend(handler).stream([user("hi")]))


# -- discovery and the connection test -------------------------------------------------


async def test_test_reports_connected_and_lists_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"id": "default-model", "owned_by": "relay"}, {"id": "auto"}]}
        )

    result = await backend(handler).test()

    assert result.result == "connected"
    assert result.models == ["default-model", "auto"]


async def test_a_model_the_server_does_not_have_lists_what_it_does_have() -> None:
    """The usual cause is a name off by a suffix, so the remedy is the actual list."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "some-other-model"}]})

    result = await backend(handler).test()

    assert result.result == "server_error"
    assert "some-other-model" in result.message


async def test_a_non_api_path_is_reported_as_the_wrong_address() -> None:
    """Pointing at a server's web UI instead of its API is a common mistake."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<!doctype html><html>")

    result = await backend(handler).test()

    assert result.result == "server_error"
    assert "OpenAI-compatible" in result.message


def test_a_loopback_endpoint_counts_as_local_and_a_remote_one_does_not() -> None:
    """This drives the privacy indicator, so it must not be optimistic."""
    local = OpenAiCompatibleBackend(endpoint="http://localhost:9090/v1")
    remote = OpenAiCompatibleBackend(endpoint="https://api.openai.com/v1")
    unparseable = OpenAiCompatibleBackend(endpoint="not a url at all")

    assert local.capabilities.local is True
    assert remote.capabilities.local is False
    assert unparseable.capabilities.local is False
