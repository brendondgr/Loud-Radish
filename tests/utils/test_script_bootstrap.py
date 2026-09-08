"""Every script puts the ROCm wheel back before it does anything else (D-064).

`app.py` repairs the build `uv run` replaces; nothing else did, so a bare `uv run` of any script
under `scripts/` broke the server's next model load. The repair now lives in
`scripts/_bootstrap.py`, and these tests are about two things: that it does what the launcher's
does, and that no script can fall off the list — a rule checked over the files rather than
remembered.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"


@pytest.fixture
def bootstrap():
    """`scripts/_bootstrap.py` loaded by path, the way a script that lives beside it finds it."""
    spec = importlib.util.spec_from_file_location(
        "loud_radish_bootstrap", SCRIPTS / "_bootstrap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_puts_the_backend_on_the_path_once(bootstrap, monkeypatch) -> None:
    from app.services.asr import acceleration

    monkeypatch.setattr(acceleration, "repair_kept_wheel", lambda: None)
    backend = str(bootstrap.BACKEND_ROOT)
    before = sys.path.count(backend)

    bootstrap.prepare()
    bootstrap.prepare()

    assert sys.path.count(backend) == max(before, 1)


def test_prepare_makes_the_launchers_repair_and_says_what_it_did(bootstrap, monkeypatch, capsys):
    from app.services.asr import acceleration

    calls: list[str] = []

    def repair() -> str:
        calls.append("repair")
        return "Restored the ROCm build of CTranslate2."

    monkeypatch.setattr(acceleration, "repair_kept_wheel", repair)

    assert bootstrap.prepare() == "Restored the ROCm build of CTranslate2."
    assert calls == ["repair"]
    assert "Restored" in capsys.readouterr().err


def test_a_repair_that_raises_does_not_stop_the_script(bootstrap, monkeypatch) -> None:
    from app.services.asr import acceleration

    def explode() -> str:
        raise RuntimeError("the package index is unreachable")

    monkeypatch.setattr(acceleration, "repair_kept_wheel", explode)

    assert bootstrap.prepare() is None


def test_the_repair_can_be_declined(bootstrap, monkeypatch) -> None:
    from app.services.asr import acceleration

    monkeypatch.setattr(acceleration, "repair_kept_wheel", lambda: pytest.fail("must not run"))

    assert bootstrap.prepare(repair_gpu=False) is None


@pytest.mark.parametrize(
    "script", sorted(p.name for p in SCRIPTS.glob("*.py") if p.name != "_bootstrap.py")
)
def test_every_script_prepares_before_anything_else(script: str) -> None:
    """A rule over the files, so a new script cannot forget what every other one does.

    Any script run through `uv run` re-syncs the environment, whether or not it imports the
    backend — so the ones that only talk to systemd or PipeWire need the repair as much as the
    ones that load a model.
    """
    source = (SCRIPTS / script).read_text(encoding="utf-8")

    assert "from _bootstrap import" in source, f"{script} does not import _bootstrap"
    assert "\nprepare()\n" in source, f"{script} never calls prepare()"
    # Before the backend is imported, or the repair reinstalls a build already loaded.
    first_app_import = source.find("from app")
    assert first_app_import == -1 or source.index("\nprepare()\n") < first_app_import, (
        f"{script} imports the backend before prepare() has run"
    )


def test_the_companion_repairs_the_wheel_before_it_starts(monkeypatch) -> None:
    """Starting the tray icon with a bare `uv run` was what swapped the wheel out from under the
    running server (docs/checklist.md). The companion now puts it back on its own launch."""
    from app.companion import main as companion_main
    from app.services.asr import acceleration

    order: list[str] = []
    monkeypatch.setattr(acceleration, "repair_kept_wheel", lambda: order.append("repair") or "ok")

    class Stub:
        def __init__(self, **_kwargs) -> None:
            order.append("companion")

        def run(self) -> int:
            return 0

    monkeypatch.setattr(companion_main, "Companion", Stub)

    assert companion_main.main(["--no-tray"]) == 0
    assert order == ["repair", "companion"]


def test_the_suite_switches_the_repair_off_for_every_test() -> None:
    """Loading a script by path runs `prepare()`; on the developer's machine that must never
    become `uv pip install`. The autouse fixture in `tests/conftest.py` is what stops it."""
    import os

    from app.services.asr import acceleration

    assert os.environ.get(acceleration.REPAIR_OFF) == "1"
    assert acceleration.repair_kept_wheel() is None
