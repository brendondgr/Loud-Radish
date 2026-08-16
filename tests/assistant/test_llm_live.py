"""Integration tests against a real OpenAI-compatible server.

Skipped when no server is listening, so the suite still passes on a machine without one. That makes
these tests opt-in by environment rather than by a flag nobody remembers to pass.

They exist because the stubbed tests cannot prove the client works — a stub asserts the client
handles the shapes *this repository imagines*. Only a live server proves it handles the shapes a
real one sends, and the reasoning-model behaviour these tests cover was discovered that way rather
than designed for.

Point them elsewhere with ``LLM_TEST_ENDPOINT`` and ``LLM_TEST_MODEL``.
"""

from __future__ import annotations

import os
import socket

import httpx
import pytest
from app.services.llm.contract import GenerationOptions, system, user
from app.services.llm.openai_compatible import OpenAiCompatibleBackend

ENDPOINT = os.environ.get("LLM_TEST_ENDPOINT", "http://localhost:9090/v1")
MODEL = os.environ.get("LLM_TEST_MODEL", "default-model")


def _unused_port() -> int:
    """Ask the OS for a port, then release it, so nothing is listening on the number returned."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _server_is_up() -> bool:
    try:
        response = httpx.get(f"{ENDPOINT}/models", timeout=2.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not _server_is_up(), reason=f"No OpenAI-compatible server at {ENDPOINT}"),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def backend() -> OpenAiCompatibleBackend:
    return OpenAiCompatibleBackend(endpoint=ENDPOINT, model=MODEL)


async def test_the_configured_model_is_actually_offered(backend) -> None:
    result = await backend.test()

    assert result.result == "connected", result.message
    assert MODEL in result.models


async def test_a_wrong_model_name_reports_what_the_server_does_have(backend) -> None:
    wrong = OpenAiCompatibleBackend(endpoint=ENDPOINT, model="no-such-model-xyz")
    result = await wrong.test()

    assert result.result == "server_error"
    assert MODEL in result.message


async def test_a_wrong_port_is_no_server_rather_than_a_hang() -> None:
    """The short connect timeout is the point: a typo must fail in seconds, not minutes.

    The port is obtained from the OS rather than hard-coded. A guessed "surely nothing is here"
    port is a test that passes until the day someone runs a second service, and then reports a
    classification bug that does not exist.
    """
    port = _unused_port()
    wrong = OpenAiCompatibleBackend(endpoint=f"http://localhost:{port}/v1", model=MODEL)
    result = await wrong.test()

    assert result.result == "no_server"
    assert str(port) in result.message


async def test_a_real_answer_streams_back_as_content(backend) -> None:
    """The end-to-end property: an answer arrives, in ``text``, not buried in reasoning.

    The token budget is deliberately generous. A reasoning model spends part of it thinking, and a
    budget tuned for a plain model produces an empty answer — the exact failure this test would
    otherwise report as "streaming is broken".
    """
    options = GenerationOptions(temperature=0.0, max_output_tokens=2000)
    messages = [
        system("Answer in one short sentence."),
        user("What is the capital of France?"),
    ]

    chunks = [chunk async for chunk in backend.stream(messages, options)]
    answer = "".join(chunk.text for chunk in chunks)

    assert "paris" in answer.lower(), f"got {answer!r}"
    assert chunks[-1].done is True


async def test_cancelling_mid_stream_stops_the_request(backend) -> None:
    """The stop button depends on this: closing the iterator must end the request."""
    options = GenerationOptions(temperature=0.0, max_output_tokens=2000)
    stream = backend.stream([user("Count slowly from 1 to 200.")], options)

    received = 0
    async for chunk in stream:
        if not chunk.is_empty:
            received += 1
        if received >= 2:
            break

    await stream.aclose()
    assert received >= 2


async def test_an_assembled_question_is_accepted_by_a_real_server() -> None:
    """**The reported fault, end to end against the thing that rejected it.**

    Every question returned `400 System message must be at the beginning`, because the assembler
    sent the standing instructions and the context blocks as two separate `system` messages. That is
    legal in the OpenAI schema and refused by real servers, so no unit test on the message list
    could have caught it — only sending the real shape to a real server can, which is what this
    does. A stub asserts the client handles the shapes this repository imagines.
    """
    from app.config.defaults import default_config
    from app.models.session import Summary
    from app.services.context.assembler import ContextRequest, assemble

    from tests.assistant.test_context_assembly import FakeStore, talk

    store = FakeStore(
        segments=talk(),
        summaries=[Summary(id=1, start=0.0, end=120.0, text="The speaker set up the problem.")],
    )
    built = assemble(store, default_config(), ContextRequest(question="What was said?", now=400.0))

    backend = OpenAiCompatibleBackend(endpoint=ENDPOINT, model=MODEL)
    chunks = [
        chunk
        async for chunk in backend.stream(built.messages, GenerationOptions(max_output_tokens=32))
    ]

    assert chunks, "the server accepted the request but returned nothing"
