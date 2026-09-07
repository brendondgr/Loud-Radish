"""Combinations that must not be loaded because they kill the process (D-053).

Almost every bad configuration in this application fails in a way something can catch: the model
raises, the message names the cause, the session degrades. **This one does not.** On an AMD GPU
through ROCm, loading `small` at `int8` aborts the process with

    Memory access fault by GPU node-1 … Reason: Page not present or supervisor privilege.

taking the server and any recording in progress with it. There is no exception, so there is nothing
to handle afterwards; the only place to stop it is before the load.

A guard derived from one machine would normally be too broad to ship. Two things justify this one:
the failure is uncatchable, and it is reachable from the **shipped defaults** — `model="small"`,
`device="auto"`, `precision="int8"` resolves to exactly this combination on any AMD machine.
"""

from __future__ import annotations

import pytest
from app.services.asr import acceleration


@pytest.fixture
def on_amd(monkeypatch):
    monkeypatch.setattr(acceleration, "_detect_hardware", lambda: ("rocm", "AMD gfx1151"))
    monkeypatch.delenv(acceleration.UNSAFE_OVERRIDE, raising=False)


@pytest.fixture
def on_nvidia(monkeypatch):
    monkeypatch.setattr(acceleration, "_detect_hardware", lambda: ("cuda", "RTX 4090"))
    monkeypatch.delenv(acceleration.UNSAFE_OVERRIDE, raising=False)


# -- what is refused ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["small", "medium", "large-v3", "Systran/faster-whisper-small"])
def test_int8_on_an_amd_gpu_is_refused_for_the_larger_models(on_amd, model) -> None:
    assert acceleration.unsafe_combination("cuda", "int8", model)


def test_the_shipped_default_is_the_combination_that_crashes(on_amd) -> None:
    """**Why this guard exists at all.** `small`/`auto`/`int8` is what ships, and `auto` resolves
    to the GPU wherever there is one."""
    from app.config.schema import AsrConfig

    shipped = AsrConfig()

    assert acceleration.unsafe_combination("auto", shipped.precision, shipped.model)


def test_the_refusal_says_what_to_do_instead(on_amd) -> None:
    reason = acceleration.unsafe_combination("cuda", "int8", "small")

    assert "float16" in reason
    assert "CPU" in reason or "cpu" in reason


# -- what is allowed ----------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["tiny", "base", "tiny.en", "base.en"])
def test_the_models_measured_working_are_allowed(on_amd, model) -> None:
    """`tiny` and `base` at int8 on the GPU were measured running correctly here — 20.5x and 22.5x
    faster than real time with sensible transcripts."""
    assert acceleration.unsafe_combination("cuda", "int8", model) == ""


def test_float16_on_the_gpu_is_allowed(on_amd) -> None:
    """`small` at float16 was measured working, so the precision is the problem, not the size."""
    assert acceleration.unsafe_combination("cuda", "float16", "small") == ""


def test_the_cpu_is_never_refused(on_amd) -> None:
    """It was also the *faster* of the two on this machine, which is its own surprise."""
    assert acceleration.unsafe_combination("cpu", "int8", "large-v3") == ""


def test_nothing_is_refused_on_nvidia(on_nvidia) -> None:
    """The fault is a ROCm one. Refusing int8 on CUDA would take away the combination most
    NVIDIA users should be running."""
    assert acceleration.unsafe_combination("cuda", "int8", "large-v3") == ""


def test_the_override_lets_someone_through(on_amd, monkeypatch) -> None:
    """A guard from one machine must be escapable by someone whose machine differs."""
    monkeypatch.setenv(acceleration.UNSAFE_OVERRIDE, "1")

    assert acceleration.unsafe_combination("cuda", "int8", "large-v3") == ""


# -- the backend consults it --------------------------------------------------------------------


def test_the_backend_refuses_before_it_loads_anything(on_amd) -> None:
    """Before, not during: there is no 'during' to fail in."""
    from app.services.asr.contract import AsrLoadError
    from app.services.asr.faster_whisper import FasterWhisperBackend

    loaded: list[str] = []
    backend = FasterWhisperBackend(
        model="small",
        device="cuda",
        precision="int8",
        model_factory=lambda *a, **k: loaded.append("loaded"),
    )

    with pytest.raises(AsrLoadError, match="GPU memory fault"):
        backend.load()

    assert loaded == [], "the model was constructed despite the refusal"
