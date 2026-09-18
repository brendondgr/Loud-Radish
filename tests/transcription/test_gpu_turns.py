"""The speech model and a local language model take turns on the GPU (D-072).

Both faults on 2026-09-18 happened while the two were computing on the iGPU at once. These tests
pin the rules that keep them apart, and the bound that keeps taking turns from ever costing audio.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import numpy as np
import pytest
from app.config.schema import AsrConfig, LlmConfig
from app.services.asr import lifecycle as lifecycle_module
from app.services.asr.contract import AsrCapabilities
from app.services.asr.lifecycle import AsrLifecycle
from app.services.asr.mock import MockAsrBackend, default_script
from app.services.gpu.turns import GpuTurns, is_loopback, shared_turns
from app.services.llm.contract import user
from app.services.llm.openai_compatible import OpenAiCompatibleBackend
from app.services.llm.registry import build_llm

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def in_thread(target) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


# -- the rules -------------------------------------------------------------------------


def test_a_speech_pass_with_nothing_else_running_does_not_wait() -> None:
    turns = GpuTurns()
    started = time.monotonic()
    with turns.speech_pass(max_wait_s=4.0) as alone:
        assert alone
    assert time.monotonic() - started < 0.05


async def test_a_language_model_request_waits_for_the_running_speech_pass() -> None:
    turns = GpuTurns()
    order: list[str] = []
    release = threading.Event()

    def speech() -> None:
        with turns.speech_pass(max_wait_s=4.0):
            order.append("speech started")
            release.wait(2.0)
            order.append("speech ended")

    in_thread(speech)
    while turns.busy["speech"] == 0:
        await asyncio.sleep(0.01)

    async def llm() -> None:
        async with turns.language_model():
            order.append("llm")

    task = asyncio.create_task(llm())
    await asyncio.sleep(0.1)
    assert order == ["speech started"]
    release.set()
    await task

    assert order == ["speech started", "speech ended", "llm"]


async def test_a_speech_pass_waits_for_the_language_model_and_then_has_the_gpu() -> None:
    turns = GpuTurns()
    result: dict[str, object] = {}

    async with turns.language_model():

        def speech() -> None:
            started = time.monotonic()
            with turns.speech_pass(max_wait_s=4.0) as alone:
                result["alone"] = alone
                result["waited"] = time.monotonic() - started

        thread = in_thread(speech)
        await asyncio.sleep(0.3)
        assert "alone" not in result

    await asyncio.to_thread(thread.join, 2.0)
    assert result["alone"] is True
    assert 0.25 < result["waited"] < 1.0
    assert turns.overlaps == 0


async def test_past_its_bound_a_speech_pass_runs_alongside_rather_than_drop_audio() -> None:
    turns = GpuTurns()
    result: dict[str, object] = {}

    async with turns.language_model():

        def speech() -> None:
            started = time.monotonic()
            with turns.speech_pass(max_wait_s=0.2) as alone:
                result["alone"] = alone
                result["waited"] = time.monotonic() - started

        thread = in_thread(speech)
        await asyncio.to_thread(thread.join, 2.0)

    assert result["alone"] is False
    assert result["waited"] < 0.5
    assert turns.overlaps == 1


async def test_a_waiting_request_is_not_starved_by_passes_back_to_back() -> None:
    """A live session in catch-up runs passes with no gap; the request still gets in between."""
    turns = GpuTurns()
    stop = threading.Event()
    passes: list[float] = []

    def live_session() -> None:
        while not stop.is_set():
            with turns.speech_pass(max_wait_s=4.0):
                passes.append(time.monotonic())
                time.sleep(0.05)

    thread = in_thread(live_session)
    await asyncio.sleep(0.1)
    started = time.monotonic()
    async with turns.language_model():
        got_in = time.monotonic() - started
    stop.set()
    await asyncio.to_thread(thread.join, 2.0)

    assert got_in < 0.5


async def test_a_request_cancelled_while_waiting_does_not_hold_up_the_speech_model() -> None:
    turns = GpuTurns()
    release = threading.Event()

    def speech() -> None:
        with turns.speech_pass(max_wait_s=4.0):
            release.wait(2.0)

    thread = in_thread(speech)
    while turns.busy["speech"] == 0:
        await asyncio.sleep(0.01)

    async def llm() -> None:
        async with turns.language_model():
            pass

    task = asyncio.create_task(llm())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.to_thread(thread.join, 2.0)

    assert turns.busy == {"speech": 0, "llm": 0, "llm_waiting": 0}
    started = time.monotonic()
    with turns.speech_pass(max_wait_s=4.0):
        pass
    assert time.monotonic() - started < 0.05


async def test_language_model_requests_do_not_wait_for_each_other() -> None:
    turns = GpuTurns()
    inside = 0
    most = 0

    async def request() -> None:
        nonlocal inside, most
        async with turns.language_model():
            inside += 1
            most = max(most, inside)
            await asyncio.sleep(0.05)
            inside -= 1

    await asyncio.gather(request(), request(), request())
    assert most == 3


# -- who takes turns -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://localhost:4000/v1", True),
        ("http://127.0.0.1:7070/v1", True),
        ("http://[::1]:8080/v1", True),
        ("http://skynet.local:9090/v1", False),
        ("https://api.openai.com/v1", False),
        ("not a url", False),
    ],
)
def test_only_this_machine_counts_as_sharing_the_gpu(endpoint: str, expected: bool) -> None:
    assert is_loopback(endpoint) is expected


def llm_config(**local) -> LlmConfig:
    return LlmConfig.model_validate({"local": {"endpoint": "http://localhost:4000/v1", **local}})


def test_a_local_server_on_this_machine_takes_turns() -> None:
    assert build_llm(llm_config())._gpu_turns is shared_turns()  # type: ignore[attr-defined]


def test_a_local_server_elsewhere_does_not() -> None:
    config = llm_config(endpoint="http://skynet.local:9090/v1")
    assert build_llm(config)._gpu_turns is None  # type: ignore[attr-defined]


def test_the_setting_overrides_the_guess_either_way() -> None:
    assert build_llm(llm_config(shares_gpu="no"))._gpu_turns is None  # type: ignore[attr-defined]
    forced = llm_config(endpoint="http://gpu-box:8080/v1", shares_gpu="yes")
    assert build_llm(forced)._gpu_turns is shared_turns()  # type: ignore[attr-defined]


def test_a_hosted_provider_never_does() -> None:
    config = LlmConfig.model_validate({"mode": "api", "api": {"provider": "openai"}})
    assert build_llm(config)._gpu_turns is None  # type: ignore[attr-defined]


async def test_a_stream_holds_the_gpu_until_its_last_chunk() -> None:
    turns = GpuTurns()
    seen: list[dict[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(turns.busy)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    backend = OpenAiCompatibleBackend(
        endpoint="http://localhost:4000/v1",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        gpu_turns=turns,
    )
    chunks = [chunk async for chunk in backend.stream([user("hi")])]

    assert chunks[-1].done
    assert seen == [{"speech": 0, "llm": 1, "llm_waiting": 0}]
    assert turns.busy["llm"] == 0


# -- the speech side -------------------------------------------------------------------


class GpuMock(MockAsrBackend):
    """The scripted model, claiming to run on the GPU."""

    @property
    def capabilities(self) -> AsrCapabilities:
        return AsrCapabilities(runs_on="cuda")


class CpuMock(MockAsrBackend):
    @property
    def capabilities(self) -> AsrCapabilities:
        return AsrCapabilities(runs_on="cpu")


async def lifecycle_with(backend_class, monkeypatch, turns: GpuTurns) -> AsrLifecycle:
    monkeypatch.setattr(
        lifecycle_module, "build_backend", lambda config: backend_class(default_script())
    )
    lifecycle = AsrLifecycle(AsrConfig(backend="mock", gpu_turn_wait_s=0.3), turns=turns)
    await lifecycle.load()
    return lifecycle


async def test_a_gpu_pass_waits_its_turn(monkeypatch) -> None:
    turns = GpuTurns()
    lifecycle = await lifecycle_with(GpuMock, monkeypatch, turns)
    audio = np.random.default_rng(0).normal(0, 0.1, 8000).astype(np.float32)

    async with turns.language_model():
        started = time.monotonic()
        await asyncio.to_thread(lifecycle.transcribe, audio)
        waited = time.monotonic() - started

    assert waited >= 0.3
    assert turns.overlaps == 1


async def test_a_cpu_pass_has_nothing_to_wait_for(monkeypatch) -> None:
    turns = GpuTurns()
    lifecycle = await lifecycle_with(CpuMock, monkeypatch, turns)
    audio = np.random.default_rng(0).normal(0, 0.1, 8000).astype(np.float32)

    async with turns.language_model():
        started = time.monotonic()
        await asyncio.to_thread(lifecycle.transcribe, audio)
        waited = time.monotonic() - started

    assert waited < 0.2
    assert turns.overlaps == 0
