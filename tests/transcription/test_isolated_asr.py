"""The speech model in its own process, and what happens when that process dies (D-071).

On 2026-09-18 a GPU memory fault aborted the server twice, and two recordings went with it. These
tests kill, stop and starve a real worker process — the scripted mock stands in for the model, but
the process, the socket and the supervision are the ones the server uses.
"""

from __future__ import annotations

import os
import signal
import threading
import time

import numpy as np
import pytest
from app.config.schema import AsrConfig
from app.services.asr import lifecycle as lifecycle_module
from app.services.asr.contract import AsrLoadError, AsrUnavailableError
from app.services.asr.isolated import IsolatedAsrBackend, WorkerDied, _describe_exit
from app.services.asr.lifecycle import AsrLifecycle
from app.services.asr.mock import MockAsrBackend, default_script
from app.services.asr.registry import build_backend
from app.services.session import degradation

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def speech(seed: int = 0, seconds: float = 0.5) -> np.ndarray:
    """Noise loud enough that the mock does not treat it as silence."""
    return np.random.default_rng(seed).normal(0, 0.1, int(16_000 * seconds)).astype(np.float32)


@pytest.fixture
def isolated():
    backends: list[IsolatedAsrBackend] = []

    def build(**kwargs) -> IsolatedAsrBackend:
        config = AsrConfig(backend="mock", **kwargs.pop("config", {}))
        backend = IsolatedAsrBackend(config, **kwargs)
        backend.load()
        backends.append(backend)
        return backend

    yield build
    for backend in backends:
        backend.unload()


def wait_for_exit(pid: int, timeout: float = 5.0) -> bool:
    """Whether ``pid`` has exited. A zombie counts: nothing has reaped it yet, but it is gone."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat") as stat:
                if stat.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except FileNotFoundError:
            return True
        time.sleep(0.05)
    return False


# -- the ordinary path -----------------------------------------------------------------


def test_a_worker_answers_as_the_model_would_in_process(isolated) -> None:
    backend = isolated()
    in_process = MockAsrBackend(default_script(), model_id="mock:small")
    in_process.load()

    assert backend.worker_pid not in (None, os.getpid())
    assert backend.model_id == "mock:small"
    assert backend.transcribe(speech()).text == in_process.transcribe(speech()).text


def test_unloading_ends_the_worker(isolated) -> None:
    backend = isolated()
    pid = backend.worker_pid
    backend.unload()

    assert backend.worker_pid is None
    assert wait_for_exit(pid)
    with pytest.raises(AsrLoadError):
        backend.transcribe(speech())


def test_the_worker_leaves_when_the_server_goes(isolated) -> None:
    """End-of-file on the socket is the server's death; nothing has to find and kill the worker."""
    backend = isolated()
    pid = backend.worker_pid
    backend._conn.close()

    assert wait_for_exit(pid)


# -- surviving the worker --------------------------------------------------------------


def test_a_worker_killed_between_passes_is_replaced_and_the_pass_still_answers(isolated) -> None:
    backend = isolated()
    notices: list[tuple[str, str]] = []
    backend.set_notice_listener(lambda kind, detail: notices.append((kind, detail)))
    first = backend.worker_pid
    os.kill(first, signal.SIGKILL)
    wait_for_exit(first)

    assert backend.transcribe(speech()).text
    assert backend.worker_pid not in (None, first)
    assert backend.restarts == 1
    assert notices[0][0] == "restarted"
    assert "SIGKILL" in notices[0][1]


def test_a_worker_that_dies_mid_pass_is_replaced_and_the_pass_made_again(isolated) -> None:
    backend = isolated()
    first = backend.worker_pid
    # Stopped, it cannot answer before it is killed — so the death lands inside the request.
    os.kill(first, signal.SIGSTOP)
    threading.Timer(0.4, os.kill, (first, signal.SIGKILL)).start()

    assert backend.transcribe(speech()).text
    assert backend.worker_pid not in (None, first)
    assert backend.restarts == 1


def test_a_worker_that_stops_answering_is_killed_and_replaced(isolated) -> None:
    backend = isolated(hang_base_s=0.5)
    first = backend.worker_pid
    os.kill(first, signal.SIGSTOP)

    started = time.monotonic()
    assert backend.transcribe(speech(seconds=0.1)).text
    assert time.monotonic() - started < 5.0
    assert wait_for_exit(first)
    assert backend.restarts == 1


def test_repeated_crashes_on_the_gpu_move_the_model_to_the_cpu(isolated) -> None:
    backend = isolated(config={"device": "cuda", "precision": "float16"}, crash_limit=2)
    notices: list[str] = []
    backend.set_notice_listener(lambda kind, _: notices.append(kind))

    for _ in range(2):
        pid = backend.worker_pid
        os.kill(pid, signal.SIGKILL)
        wait_for_exit(pid)
        backend.transcribe(speech())

    assert notices == ["restarted", "fell-back"]
    assert backend.fell_back
    assert backend._config.device == "cpu"
    assert backend._config.precision == "int8"


def test_a_second_death_in_one_pass_is_reported_not_looped(isolated, monkeypatch) -> None:
    backend = isolated()

    real = backend._request_locked

    def every_pass_dies(message, limit):
        if message[0] == "transcribe":
            raise WorkerDied("gone")
        return real(message, limit)

    monkeypatch.setattr(backend, "_request_locked", every_pass_dies)
    with pytest.raises(WorkerDied):
        backend.transcribe(speech())
    assert backend.restarts == 1


def test_an_abort_is_named_for_what_it_usually_means() -> None:
    assert "memory fault" in _describe_exit(-signal.SIGABRT)
    assert _describe_exit(-signal.SIGKILL) == "SIGKILL"
    assert _describe_exit(3) == "exit code 3"


# -- which backends are isolated -------------------------------------------------------


def test_faster_whisper_runs_in_its_own_process_by_default() -> None:
    assert isinstance(build_backend(AsrConfig(backend="faster-whisper")), IsolatedAsrBackend)


def test_isolation_can_be_switched_off() -> None:
    config = AsrConfig(backend="faster-whisper", process_isolation=False)
    assert not isinstance(build_backend(config), IsolatedAsrBackend)


def test_the_mock_stays_in_process() -> None:
    assert isinstance(build_backend(AsrConfig(backend="mock")), MockAsrBackend)


def test_an_unknown_backend_fails_before_any_process_starts() -> None:
    with pytest.raises(AsrUnavailableError):
        IsolatedAsrBackend(AsrConfig(backend="no-such-model"))


# -- reaching the interface ------------------------------------------------------------


async def test_the_lifecycle_passes_restarts_on_to_whoever_is_listening(monkeypatch) -> None:
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lifecycle_module, "build_backend", lambda config: IsolatedAsrBackend(config)
    )
    lifecycle = AsrLifecycle(
        AsrConfig(backend="mock"), on_notice=lambda kind, detail: notices.append((kind, detail))
    )
    await lifecycle.load()
    try:
        pid = lifecycle.backend.worker_pid
        os.kill(pid, signal.SIGKILL)
        wait_for_exit(pid)
        lifecycle.transcribe(speech())
    finally:
        await lifecycle.unload()

    assert [kind for kind, _ in notices] == ["restarted"]


def test_the_warnings_say_the_recording_carried_on() -> None:
    restarted = degradation.asr_worker_restarted("The process ended (SIGABRT).")
    assert restarted.transcription_continues
    assert "recording was not affected" in restarted.message

    fell_back = degradation.asr_fell_back_to_cpu("faster-whisper:small:float16")
    assert "CPU" in fell_back.message
    assert fell_back.remedy == {"asr.model": "base"}
