"""The `radish` terminal command (D-056).

A shell script cannot be unit-tested the way the rest of this repository is, and most of what it
does — detaching a server, killing it again — is only true against a real machine. What *can* be
pinned is the part that rots silently: **the two command vocabularies staying in step.**

`radish` owns `start`, `stop`, `restart` and so on, and hands `toggle`, `arm` and `dictate` to
`utils/loud_radish_ctl.py`. Rename a command in either place and the failure appears at a terminal,
weeks later, as "unknown command" — which is the sort of thing nobody notices until they need it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "radish"

#: What `radish` handles itself. `start`/`stop` here mean the *application*, and deliberately not
#: what the same words mean to the session tool.
OWN_COMMANDS = frozenset(
    {"start", "stop", "restart", "status", "logs", "open", "settings", "install", "uninstall"}
)

#: What it forwards to `loud_radish_ctl.py`.
DELEGATED = frozenset({"toggle", "arm", "dictate"})


@pytest.fixture(scope="module")
def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def run(*args: str) -> subprocess.CompletedProcess:
    """Run the script with a port nothing is listening on, so it cannot reach a real server."""
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "API_PORT": "1"},
        check=False,
    )


# -- the file itself ---------------------------------------------------------------------------


def test_the_script_exists_and_can_be_run() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK), (
        "not executable, so a symlink into ~/.local/bin will not work"
    )


def test_the_script_is_valid_bash() -> None:
    """`bash -n` parses without executing. Catches an unclosed quote before a user does."""
    done = subprocess.run(  # noqa: S603
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr


# -- the two vocabularies ----------------------------------------------------------------------


def test_every_delegated_command_exists_in_the_control_script(source) -> None:
    """**The invariant this file is for.** `radish dictate` forwards a word that
    `loud_radish_ctl.py` must still accept."""
    control = (REPO / "utils" / "loud_radish_ctl.py").read_text(encoding="utf-8")
    known = set(re.findall(r'\("(\w+)", cmd_\w+,', control))

    assert DELEGATED <= known, (
        f"radish forwards commands the control script no longer has: {DELEGATED - known}"
    )


def test_the_delegated_commands_are_the_ones_the_script_actually_forwards(source) -> None:
    [branch] = re.findall(r"\n\s*(toggle \| arm \| dictate)\)", source)

    assert set(branch.split(" | ")) == DELEGATED


def test_the_two_vocabularies_do_not_overlap() -> None:
    """`stop` means the application here and a recording there. If a word ever appeared in both,
    the more surprising meaning would win silently."""
    assert not (OWN_COMMANDS & DELEGATED)


@pytest.mark.parametrize("command", sorted(OWN_COMMANDS))
def test_each_of_its_own_commands_has_a_branch(source, command) -> None:
    assert re.search(rf"^\s*{command}[ )|]", source, re.M), (
        f"'{command}' is documented but unhandled"
    )


# -- behaviour that needs no server --------------------------------------------------------------


def test_help_explains_itself_and_succeeds() -> None:
    done = run("help")

    assert done.returncode == 0
    assert "radish" in done.stdout
    for command in ("start", "stop", "status", "dictate"):
        assert command in done.stdout


def test_the_help_warns_that_stop_is_not_stop(source) -> None:
    """The one genuinely confusable thing about this command."""
    done = run("--help")

    assert "stops the application" in done.stdout


def test_an_unknown_command_says_so_and_shows_the_usage() -> None:
    """**Not forwarded to the session tool.** A blanket fallthrough answers a typo with *its*
    usage, whose `start` and `stop` mean a recording — the most misleading help available."""
    done = run("restrat")

    assert done.returncode == 2
    assert "unknown command 'restrat'" in done.stderr
    assert "radish" in done.stderr


def test_status_reports_on_both_processes_whatever_their_state() -> None:
    """**Not "asserts nothing is running".** The first version of this test did exactly that and
    failed the moment the developer had the application open — a claim about a machine the test
    does not own, which is the mistake `tests/conftest.py` records four times over. What `status`
    promises is a line about each process and a zero exit, and that holds either way."""
    done = run("status")

    assert done.returncode == 0
    assert "server" in done.stdout
    assert "tray icon" in done.stdout
    assert re.search(r"server\s+(running|not running)", done.stdout)


# -- the two things that were got wrong while writing it ------------------------------------------


def test_processes_are_started_detached_with_fork(source) -> None:
    """Without `--fork` the subshell waits on its child and holds the terminal's stdout open, so
    `radish start | anything` never finishes. Watched happening."""
    assert "setsid --fork" in source
    assert "</dev/null" in source


def test_the_companion_is_started_without_a_sync(source) -> None:
    """A plain `uv run` replaces the ROCm build of CTranslate2 with the PyPI one and breaks the
    *server's* next model load. `app.py` repairs that on its own launch; the companion does not."""
    assert "uv run --no-sync python -m app.companion.main" in source


def test_the_process_patterns_are_anchored(source) -> None:
    """An unanchored `pgrep -f app.py` matches the shell running this very script, so `radish stop`
    kills itself mid-command. It did."""
    assert 'pgrep -f -- "^$1"' in source
