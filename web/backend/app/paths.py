"""Filesystem locations the application needs, resolved from this module's own position.

Keeping path resolution in one place means no module has to guess how deep it sits in the tree, and
the frontend directories move by editing one function rather than every mount site.
"""

from __future__ import annotations

from pathlib import Path

#: Repository root — this file is at ``<root>/web/backend/app/paths.py``.
REPO_ROOT: Path = Path(__file__).resolve().parents[3]

WEB_DIR: Path = REPO_ROOT / "web"
BACKEND_DIR: Path = WEB_DIR / "backend"
FRONTEND_DIR: Path = WEB_DIR / "frontend"
CONTRACTS_DIR: Path = WEB_DIR / "shared" / "contracts"

TEMPLATES_DIR: Path = FRONTEND_DIR / "templates"
STATIC_DIR: Path = FRONTEND_DIR / "static"

DATA_DIR: Path = REPO_ROOT / "data"
#: Audio captured by `recorded` and `window` sessions. Deliberately *not* ``data/audio``, which is
#: the library of recordings the file source replays — one is the machine's own output and the
#: other is the user's input, and a single list mixing them invites deleting the wrong thing.
RECORDINGS_DIR: Path = DATA_DIR / "recordings"
LOGS_DIR: Path = REPO_ROOT / "logs"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` and its parents if absent, then return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
