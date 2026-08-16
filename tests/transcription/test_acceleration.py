"""Detecting GPU acceleration, and saying what is stopping it.

Three separate things have to line up before a model runs on a GPU, and all three failures look
identical from the application — no GPU option, or a load error when the user presses record. The
tests here are about telling them apart, because they have three different remedies.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest
from app.services.asr import acceleration

from app import paths


@pytest.fixture
def machine(monkeypatch, kept_wheels):
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
        # No kept wheel unless a test puts one there, so the download steps are the default.
        monkeypatch.setattr(acceleration, "KEPT_WHEEL_DIR", kept_wheels)

    return configure


@pytest.fixture
def kept_wheels(tmp_path):
    """An empty stand-in for ``data/wheels`` — the real one must never decide a test's outcome."""
    directory = tmp_path / "wheels"
    directory.mkdir()
    return directory


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


def test_a_kept_wheel_turns_three_steps_into_one(machine, kept_wheels) -> None:
    """The wheel is downloaded once and clobbered repeatedly — `uv sync` reinstalls from PyPI.

    Re-downloading forty megabytes to undo that is the wrong instruction when the file is already
    on disk, and the three-step version is where the trailing-dash mistake gets made.
    """
    wheel = kept_wheels / "ctranslate2-4.8.1-cp312-cp312-manylinux_2_28_x86_64.whl"
    wheel.touch()
    machine(hardware="rocm", name="AMD GPU (gfx1151)")

    remedy = acceleration.detect().remedy

    assert len(remedy) == 1
    assert remedy[0].startswith("uv pip install --reinstall")
    assert wheel.name in remedy[0]
    assert "curl" not in remedy[0]


def test_a_wheel_for_another_python_is_ignored(machine, kept_wheels) -> None:
    """It will not install, and a command that fails is worse than one that downloads."""
    (kept_wheels / "ctranslate2-4.8.1-cp311-cp311-manylinux_2_28_x86_64.whl").touch()
    machine(hardware="rocm", name="AMD GPU (gfx1151)")

    assert any("curl" in step for step in acceleration.detect().remedy)


def test_the_download_path_says_to_keep_the_wheel(machine, kept_wheels) -> None:
    """Otherwise the next sync costs another download — which is how this became recurring."""
    machine(hardware="rocm", name="AMD GPU (gfx1151)")

    steps = acceleration.detect().remedy

    assert steps[-1].startswith("mkdir -p")
    assert str(kept_wheels) in steps[-1]


def test_the_kept_wheel_location_is_the_one_the_documentation_names() -> None:
    """The remedy and ``docs/deployment.md`` have to agree, or one of them sends people wrong."""
    assert acceleration.KEPT_WHEEL_DIR == paths.WHEELS_DIR
    assert acceleration.KEPT_WHEEL_DIR.parent.name == "data"
    assert acceleration.KEPT_WHEEL_DIR.name == "wheels"


def test_missing_libraries_are_still_installed_before_the_kept_wheel(machine, kept_wheels) -> None:
    """A wheel that loads no libraries is not a fix; order is part of the remedy."""
    (kept_wheels / "ctranslate2-4.8.1-cp312-cp312-manylinux_2_28_x86_64.whl").touch()
    machine(hardware="rocm", missing=("libhiprand.so.1",))

    remedy = acceleration.detect().remedy

    assert remedy[0].startswith("sudo dnf install")
    assert remedy[1].startswith("uv pip install")


def test_transcription_not_installed_is_reported_before_anything_about_gpus(machine) -> None:
    """Telling someone to fix their ROCm when they have no speech model at all is noise."""
    machine(hardware="rocm", name="AMD GPU", ct2_installed=False)
    report = acceleration.detect()

    assert report.remedy == ["uv sync"]
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


# -- putting the ROCm build back --------------------------------------------------------------
#
# `uv run app.py` synchronises against the lockfile before Python starts, and the lockfile says
# PyPI. So the command used to start the application is the command that breaks its GPU support,
# on every launch. Documenting a fix does not survive that; the launcher has to undo it.


@pytest.fixture
def repairable(monkeypatch, kept_wheels):
    """A machine where the repair should fire, with the install command captured rather than run."""
    wheel = kept_wheels / "ctranslate2-4.8.1-cp314-cp314-manylinux_2_28_x86_64.whl"
    wheel.touch()
    monkeypatch.setattr(acceleration, "KEPT_WHEEL_DIR", kept_wheels)
    monkeypatch.setattr(acceleration, "_detect_hardware", lambda: ("rocm", "AMD GPU (gfx1151)"))
    monkeypatch.setattr(acceleration, "_installed_version", lambda: "4.8.1")
    monkeypatch.setattr(acceleration, "_python_tag", lambda: "314")
    monkeypatch.setattr(acceleration, "_installed_from", lambda _wheel: False)
    monkeypatch.setattr(acceleration.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.delenv(acceleration.REPAIR_OFF, raising=False)

    commands: list[list[str]] = []

    def record(command, **_kwargs):  # noqa: ANN001, ANN202
        commands.append(list(command))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", record)
    return types.SimpleNamespace(wheel=wheel, commands=commands)


def test_the_launcher_puts_the_rocm_build_back(repairable) -> None:
    message = acceleration.repair_kept_wheel()

    assert repairable.commands == [
        ["/usr/bin/uv", "pip", "install", "--quiet", "--reinstall", str(repairable.wheel)]
    ]
    assert "Restored" in message


def test_an_already_correct_environment_is_left_alone(repairable, monkeypatch) -> None:
    """Otherwise every launch pays for a reinstall, and the message cries wolf."""
    monkeypatch.setattr(acceleration, "_installed_from", lambda _wheel: True)

    assert acceleration.repair_kept_wheel() is None
    assert repairable.commands == []


def test_a_machine_with_no_amd_gpu_is_never_touched(repairable, monkeypatch) -> None:
    """An NVIDIA or CPU-only machine wants the PyPI build; replacing it would be the bug."""
    monkeypatch.setattr(acceleration, "_detect_hardware", lambda: ("cuda", "NVIDIA RTX 4090"))

    assert acceleration.repair_kept_wheel() is None
    assert repairable.commands == []


def test_no_kept_wheel_means_nothing_to_restore(repairable, monkeypatch) -> None:
    monkeypatch.setattr(acceleration, "_kept_wheel", lambda _version: None)

    assert acceleration.repair_kept_wheel() is None
    assert repairable.commands == []


def test_it_can_be_switched_off(repairable, monkeypatch) -> None:
    """For anyone who wants the environment left exactly as the lockfile describes it."""
    monkeypatch.setenv(acceleration.REPAIR_OFF, "1")

    assert acceleration.repair_kept_wheel() is None
    assert repairable.commands == []


def test_a_failed_reinstall_is_reported_rather_than_swallowed(repairable, monkeypatch) -> None:
    """Silence here would leave the user with a GPU error and no idea a repair was attempted."""

    def failing(command, **_kwargs):  # noqa: ANN001, ANN202
        return types.SimpleNamespace(returncode=1, stdout="", stderr="no matching distribution\n")

    monkeypatch.setattr("subprocess.run", failing)
    message = acceleration.repair_kept_wheel()

    assert "Could not restore" in message
    assert "no matching distribution" in message


def test_origin_is_read_from_the_installers_own_record() -> None:
    """`direct_url.json` is written for a file install and omitted for an index install.

    That distinction is the whole basis for running this on every launch, so it is checked against
    the real installed distribution rather than a fixture.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        installed = distribution("ctranslate2")
    except PackageNotFoundError:
        pytest.skip("ctranslate2 is not installed")

    raw = installed.read_text("direct_url.json")
    expected = raw is not None and "ctranslate2" in raw

    assert acceleration._installed_from(Path("nothing-matches-this.whl")) is False
    if expected:
        import json

        name = Path(json.loads(raw)["url"]).name
        assert acceleration._installed_from(Path(name)) is True


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
