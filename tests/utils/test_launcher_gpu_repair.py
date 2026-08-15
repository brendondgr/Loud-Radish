"""The launcher undoing what launching did to the GPU build.

``uv run app.py`` synchronises the environment against the lockfile before this repository's code
runs, and the lockfile names PyPI — which ships a CPU-and-CUDA CTranslate2. On an AMD machine the
command that starts the application is therefore the command that removes its GPU support, every
single time. That makes the repair part of starting up rather than a documented recovery step, and
these tests are about it happening in the right order and never being fatal.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def launcher():
    """Load ``app.py`` under a name of its own.

    It cannot be imported as ``app``: that name belongs to the backend package, and the collision is
    the exact hazard ``_prepare_import_path`` exists to prevent.
    """
    spec = importlib.util.spec_from_file_location("transcriber_launcher", REPO_ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["transcriber_launcher"] = module
    spec.loader.exec_module(module)
    return module


def test_the_repair_runs_before_the_capability_list(launcher, monkeypatch, capsys) -> None:
    """Order matters: the report below it reads the build the repair just installed."""
    monkeypatch.setattr(launcher, "_capabilities", list)
    monkeypatch.setattr(launcher, "_acceleration_lines", lambda: ["    GPU acceleration is here."])

    launcher._print_banner("127.0.0.1", 8395, reload=False, repaired="Restored the ROCm build.")
    output = capsys.readouterr().out

    assert output.index("Restored the ROCm build.") < output.index("GPU acceleration is here.")


def test_nothing_is_said_when_nothing_was_repaired(launcher, monkeypatch, capsys) -> None:
    """The ordinary case is silence — a line on every launch is a line nobody reads."""
    monkeypatch.setattr(launcher, "_capabilities", list)
    monkeypatch.setattr(launcher, "_acceleration_lines", list)

    launcher._print_banner("127.0.0.1", 8395, reload=False, repaired=None)

    assert "Restored" not in capsys.readouterr().out


def test_it_delegates_to_the_acceleration_module(launcher, monkeypatch) -> None:
    """The launcher holds no logic of its own (``docs/structure.md``); it calls the backend."""
    from app.services.asr import acceleration

    monkeypatch.setattr(acceleration, "repair_kept_wheel", lambda: "Restored it.")

    assert launcher._repair_gpu_build() == "Restored it."


def test_a_repair_that_raises_does_not_stop_the_application(launcher, monkeypatch) -> None:
    """Transcription still works on the CPU. Refusing to start over a failed repair is worse."""
    from app.services.asr import acceleration

    def explode() -> str:
        raise RuntimeError("the package index is unreachable")

    monkeypatch.setattr(acceleration, "repair_kept_wheel", explode)

    assert launcher._repair_gpu_build() is None
