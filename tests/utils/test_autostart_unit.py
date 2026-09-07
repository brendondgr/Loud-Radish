"""Installing autostart under the new unit name must remove the old one (D-038).

Not a cosmetic rename. Both units run `uv run app.py`, and the application binds a fixed port — so
two enabled units do not coexist, they race, and the loser fails on a port the winner already holds.
The failure mode is a machine that autostarts fine one boot and not the next.

The systemd calls are stubbed. These tests must never touch the developer's own session: enabling a
real unit here would install the application onto the machine running the test suite.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def autostart(monkeypatch, tmp_path):
    """``scripts/install_autostart.py``, with its unit directory redirected and systemd stubbed."""
    spec = importlib.util.spec_from_file_location(
        "loud_radish_autostart", REPO_ROOT / "scripts" / "install_autostart.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["loud_radish_autostart"] = module
    spec.loader.exec_module(module)

    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "UNIT_DIR", unit_dir)

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(module, "systemctl", lambda *args: calls.append(args) or _ok())
    module.calls = calls
    return module


class _ok:
    returncode = 0
    stderr = ""
    stdout = ""


def test_the_pre_rename_unit_is_disabled_and_deleted(autostart):
    legacy = autostart.UNIT_DIR / autostart.LEGACY_UNIT_NAME
    legacy.write_text("[Unit]\nDescription=old\n")

    assert autostart.remove_legacy_unit() is True
    assert not legacy.exists()
    assert ("disable", "--now", autostart.LEGACY_UNIT_NAME) in autostart.calls


def test_nothing_is_disabled_when_there_is_no_pre_rename_unit(autostart):
    assert autostart.remove_legacy_unit() is False
    assert autostart.calls == []


def test_installing_removes_the_old_unit_before_writing_the_new_one(autostart, monkeypatch):
    monkeypatch.setattr(autostart.shutil, "which", lambda _: "/usr/bin/uv")
    legacy = autostart.UNIT_DIR / autostart.LEGACY_UNIT_NAME
    legacy.write_text("[Unit]\nDescription=old\n")

    assert autostart.install() == 0

    assert not legacy.exists(), "two enabled units would race for the same port"
    assert (autostart.UNIT_DIR / autostart.UNIT_NAME).is_file()
    assert ("enable", "--now", autostart.UNIT_NAME) in autostart.calls


def test_uninstalling_removes_both_names(autostart):
    (autostart.UNIT_DIR / autostart.LEGACY_UNIT_NAME).write_text("[Unit]\n")
    (autostart.UNIT_DIR / autostart.UNIT_NAME).write_text("[Unit]\n")

    assert autostart.uninstall() == 0

    assert not (autostart.UNIT_DIR / autostart.LEGACY_UNIT_NAME).exists()
    assert not (autostart.UNIT_DIR / autostart.UNIT_NAME).exists(), (
        "an uninstall that leaves either behind still starts the application with the session"
    )
