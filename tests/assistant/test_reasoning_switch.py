"""One switch asks every model not to think, and a server that refuses it is asked once (D-073).

The two recordings that died on 2026-09-18 did so during a rolling summary that never asked the
model to skip its reasoning: 2,687 tokens on a GPU the speech model was using. Only the polish pass
and the dictation tidy sent the fields then, so these tests are about the client — the one place
every caller passes through.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.config.schema import AppConfig, LlmConfig
from app.config.store import RETIRED_PATHS, _drop_retired
from app.services.llm import openai_compatible
from app.services.llm.contract import GenerationOptions, user
from app.services.llm.errors import LlmServerError
from app.services.llm.openai_compatible import NO_REASONING_EXTRAS, OpenAiCompatibleBackend
from app.services.llm.registry import build_llm

pytestmark = pytest.mark.anyio

DONE = b"data: [DONE]\n\n"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _forget_refusals():
    openai_compatible._REFUSES_REASONING_FIELDS.clear()
    yield
    openai_compatible._REFUSES_REASONING_FIELDS.clear()


def recording(bodies: list[dict], statuses: list[int] | None = None):
    """A transport that records each request body and answers with the next status."""
    queue = list(statuses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        status = queue.pop(0) if queue else 200
        if status != 200:
            return httpx.Response(status, text="unknown field: think")
        return httpx.Response(200, content=DONE)

    return handler


def client(handler, endpoint: str = "http://localhost:4000/v1", **kwargs):
    return OpenAiCompatibleBackend(
        endpoint=endpoint,
        model="local",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


async def drain(backend: OpenAiCompatibleBackend, options: GenerationOptions | None = None):
    return [chunk async for chunk in backend.stream([user("hi")], options)]


async def test_every_request_carries_the_fields_when_switched_on() -> None:
    bodies: list[dict] = []
    await drain(client(recording(bodies), disable_reasoning=True))

    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert bodies[0]["reasoning_effort"] == "none"
    assert bodies[0]["think"] is False


async def test_nothing_is_added_when_switched_off() -> None:
    bodies: list[dict] = []
    await drain(client(recording(bodies), disable_reasoning=False))

    assert not set(NO_REASONING_EXTRAS) & set(bodies[0])


async def test_a_callers_own_field_wins() -> None:
    bodies: list[dict] = []
    options = GenerationOptions(extra={"reasoning_effort": "low"})
    await drain(client(recording(bodies), disable_reasoning=True), options)

    assert bodies[0]["reasoning_effort"] == "low"
    assert bodies[0]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_a_refusal_is_retried_once_without_the_fields_and_remembered() -> None:
    bodies: list[dict] = []
    backend = client(recording(bodies, statuses=[400]), disable_reasoning=True)
    chunks = await drain(backend)

    assert chunks[-1].done
    assert "think" in bodies[0]
    assert "think" not in bodies[1]

    # A new backend for the same endpoint — the way every caller builds one — skips the refusal.
    await drain(client(recording(bodies), disable_reasoning=True))
    assert "think" not in bodies[2]
    assert len(bodies) == 3


async def test_a_server_error_is_not_mistaken_for_a_refusal() -> None:
    bodies: list[dict] = []
    backend = client(recording(bodies, statuses=[503]), disable_reasoning=True)

    with pytest.raises(LlmServerError):
        await drain(backend)
    assert len(bodies) == 1
    assert not openai_compatible._REFUSES_REASONING_FIELDS


async def test_the_default_configuration_asks_every_model_not_to_think() -> None:
    config = LlmConfig.model_validate({"local": {"endpoint": "http://localhost:4000/v1"}})
    assert config.generation.disable_reasoning is True
    assert build_llm(config)._disable_reasoning is True  # type: ignore[attr-defined]

    api = LlmConfig.model_validate({"mode": "api", "api": {"provider": "openai"}})
    assert build_llm(api)._disable_reasoning is True  # type: ignore[attr-defined]


def test_the_old_polish_only_switch_is_dropped_rather_than_rejecting_the_file() -> None:
    raw = {"polish": {"disable_reasoning": False, "enabled": True}}
    assert "polish.disable_reasoning" in RETIRED_PATHS
    assert _drop_retired(raw) == ["polish.disable_reasoning"]
    AppConfig.model_validate(raw)
