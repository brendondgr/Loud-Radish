"""Detecting GPU acceleration, and saying what is stopping it.

Three separate things have to line up before a model runs on a GPU, and all three failures look
identical from the application — no GPU option, or a load error when the user presses record. The
tests here are about telling them apart, because they have three different remedies.
"""

from __future__ import annotations

import pytest
from app.services.asr import acceleration


@pytest.fixture
def machine(monkeypatch):
    """Describe a hypothetical machine: what hardware, what CTranslate2, what libraries."""

    def configure(
        *,
        hardware: str = "none",
        name: str = "",
        ct2_installed: bool = True,
        gpu_usable: bool = False,
        precisions: tuple[str, ...] = (),
        missing: tuple[str, ...] = (),
    ):
        monkeypatch.setattr(acceleration, "_detect_hardware", lambda: (hardware, name))
        monkeypatch.setattr(acceleration, "_installed", lambda module: ct2_installed)
        monkeypatch.setattr(
            acceleration, "_ctranslate2_gpu_support", lambda: (gpu_usable, list(precisions))
        )
        monkeypatch.setattr(acceleration, "_missing_rocm_libraries", lambda: list(missing))
        monkeypatch.setattr(acceleration, "_ctranslate2_version", lambda: "4.8.1")
        monkeypatch.setattr(acceleration, "_python_tag", lambda: "312")

    return configure


def test_a_working_gpu_says_so_and_offers_nothing_to_fix(machine) -> None:
    machine(
        hardware="rocm",
        name="AMD Radeon 8060S",
        gpu_usable=True,
        precisions=("float16", "int8_float16"),
    )
    report = acceleration.detect()

    assert report.usable is True
    assert report.remedy == []
    assert "AMD Radeon 8060S" in report.summary


def test_no_gpu_is_stated_plainly_rather_than_as_a_problem(machine) -> None:
    """A CPU-only machine is an ordinary configuration, not a fault."""
    machine(hardware="none")
    report = acceleration.detect()

    assert report.gpu_present is False
    assert report.remedy == []
    assert "CPU" in report.summary


def test_a_gpu_with_missing_libraries_names_them_and_the_packages(machine) -> None:
    """Observed on the development machine: everything installed except two libraries."""
    machine(
        hardware="rocm",
        name="AMD GPU (gfx1151)",
        missing=("libhiprand.so.1", "librocrand.so.1"),
    )
    report = acceleration.detect()

    assert report.usable is False
    assert report.missing_libraries == ["libhiprand.so.1", "librocrand.so.1"]
    assert "libhiprand.so.1" in report.summary
    assert any("hiprand rocrand" in step for step in report.remedy)


def test_a_gpu_with_the_wrong_build_says_that_instead(machine) -> None:
    """Libraries present, still no GPU: the PyPI CPU/CUDA wheel is installed, not the ROCm one."""
    machine(hardware="rocm", name="AMD GPU (gfx1151)", missing=())
    report = acceleration.detect()

    assert report.missing_libraries == []
    assert "ROCm build" in report.summary
    # No package install step, because nothing is missing at the system level.
    assert not any(step.startswith("sudo") for step in report.remedy)


def test_the_remedy_names_this_pythons_wheel_and_this_ctranslate2(machine) -> None:
    """A generic instruction would send the user to a wheel that will not install."""
    machine(hardware="rocm", missing=("libhiprand.so.1",))
    steps = " ".join(acceleration.detect().remedy)

    assert "v4.8.1" in steps
    assert "cp312" in steps


def test_transcription_not_installed_is_reported_before_anything_about_gpus(machine) -> None:
    """Telling someone to fix their ROCm when they have no speech model at all is noise."""
    machine(hardware="rocm", name="AMD GPU", ct2_installed=False)
    report = acceleration.detect()

    assert report.remedy == ["uv sync --extra asr-whisper"]
    assert "not installed" in report.summary


def test_an_nvidia_card_gets_a_different_remedy_from_an_amd_one(machine) -> None:
    machine(hardware="cuda", name="NVIDIA RTX 4090")
    report = acceleration.detect()

    assert report.missing_libraries == []
    assert not any("rocm" in step.lower() for step in report.remedy)


# -- the banner ---------------------------------------------------------------------------


def test_the_banner_leads_with_the_summary_then_the_steps(machine) -> None:
    machine(hardware="rocm", name="AMD GPU", missing=("libhiprand.so.1",))
    lines = acceleration.describe_lines()

    assert lines[0].startswith("AMD GPU is present")
    assert len(lines) > 1


def test_a_working_gpu_is_told_which_precision_to_choose(machine) -> None:
    """Detection is only useful if it ends in something the user can act on."""
    machine(hardware="rocm", name="AMD GPU", gpu_usable=True, precisions=("float32", "float16"))
    lines = acceleration.describe_lines()

    assert any("float16" in line for line in lines)


# -- the real machine ------------------------------------------------------------------------


def test_detection_runs_here_without_raising() -> None:
    """It runs at startup on every machine, so it must never be the reason one will not start."""
    report = acceleration.detect()

    assert report.hardware in ("cuda", "rocm", "none")
    assert isinstance(report.as_dict()["remedy"], list)


def test_health_reports_acceleration(tmp_path) -> None:
    from app.config import ConfigStore
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app(config=ConfigStore(config_path=tmp_path / "config.json"))
    with TestClient(app) as client:
        body = client.get("/api/health").json()

    assert set(body["acceleration"]) >= {"hardware", "usable", "summary", "remedy"}
