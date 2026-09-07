"""The old name must not creep back into the source (D-038).

A rebrand is not a state, it is an event — and without a guard it decays. The next feature to
hardcode "Live Seminar Transcriber" in a page title, or to reach for `transcriber-config.json`
because that is what the file used to be called, would do so unnoticed and the project would be back
to answering to two names.

So this scans the source for the *product* name and the *identifiers*, and allows them only where
`branding.py` and its consumers deliberately keep them in order to migrate from them.

What it deliberately does **not** flag is the word "transcriber" on its own. It is an ordinary
English noun that this codebase uses about forty times, correctly — "what a broken transcriber looks
like", "the transcriber will never catch up" — and a check that banned it would either be switched
off or would push people into writing worse sentences.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where application code lives. Documentation is checked by reading it, not by grep: a doc can
#: legitimately discuss the old name, and `docs/plans/` is a historical record that is never
#: rewritten.
SCANNED = ("web", "utils", "scripts", "tests")
SCANNED_FILES = ("app.py", "pyproject.toml", ".env.example")

SCANNED_SUFFIXES = {".py", ".js", ".css", ".html", ".toml", ".json", ".in", ".example", ""}

#: Strings that must not appear outside the sites listed below. Each is a *product* name or a
#: machine identifier — never the bare common noun.
FORBIDDEN = (
    "Live Seminar Transcriber",
    "TranscriberPrototype",
    "transcriber-prototype",
    "transcriber-config.json",
    "transcriber-export:",
    "TRANSCRIBER_CONFIG_PATH",
    "TRANSCRIBER_ROOT",
    "TRANSCRIBER_NO_GPU_REPAIR",
    "transcriber_ctl",
    "transcriber.service",
    "transcriber.desktop",
)

#: Files permitted to contain a forbidden string, and the reason each is permitted. Anything not
#: listed here fails, which is the point: adding a file to this list should require an argument.
ALLOWED: dict[str, str] = {
    "web/backend/app/branding.py": "declares the legacy values every migration reads",
    ".env.example": "documents the deprecated variables as deprecated",
    "tests/utils/test_no_legacy_brand.py": "this file names them in order to forbid them",
    "tests/utils/test_branding.py": "asserts the legacy constants differ from current ones",
    "scripts/install_autostart.py": (
        "a standalone script with no import path into the app package; one literal beats the "
        "sys.path juggling importing branding would take"
    ),
}

# Everything that actually performs a migration — the config store, the credential store, the tap
# sweep, the acceleration and companion env fallbacks, the shortcut re-registration, the autostart
# installer — is deliberately absent from ALLOWED. They reach the old values through `branding`
# rather than retyping them, which is the property this whole arrangement exists to produce, and
# needing an entry here would mean one of them had stopped doing that.


def _source_files() -> list[Path]:
    found: list[Path] = []
    for name in SCANNED_FILES:
        path = REPO_ROOT / name
        if path.is_file():
            found.append(path)
    for directory in SCANNED:
        for path in (REPO_ROOT / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix in SCANNED_SUFFIXES:
                found.append(path)
    return found


def test_there_are_files_to_scan():
    """A guard that silently scans nothing passes forever and protects nothing."""
    assert len(_source_files()) > 100


@pytest.mark.parametrize("forbidden", FORBIDDEN)
def test_a_pre_rename_identifier_appears_only_where_it_migrates(forbidden: str):
    offenders = []
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if forbidden in text:
            offenders.append(relative)

    assert not offenders, (
        f"{forbidden!r} is a pre-rename name (D-038) and appears in: {sorted(offenders)}. "
        "Use the constant in web/backend/app/branding.py. If this file genuinely needs the old "
        "value in order to migrate away from it, add it to ALLOWED with the reason."
    )


def test_every_allowance_is_still_earned():
    """An allowance for a file that no longer contains a legacy name is stale, and a stale
    allowance is how the next one gets waved through without an argument."""
    unused = []
    for relative, reason in ALLOWED.items():
        path = REPO_ROOT / relative
        if not path.is_file():
            unused.append(f"{relative} (missing — {reason})")
            continue
        text = path.read_text(encoding="utf-8")
        if not any(forbidden in text for forbidden in FORBIDDEN):
            unused.append(f"{relative} (no longer needs it — {reason})")

    assert not unused, f"Remove these from ALLOWED: {unused}"
