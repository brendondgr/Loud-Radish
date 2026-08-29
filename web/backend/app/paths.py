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
#: Audio and video captured by `recorded` and `window` sessions. Deliberately *not* ``data/audio``,
#: which is the library of recordings the file source replays — one is the machine's own output and
#: the other is the user's input, and a single list mixing them invites deleting the wrong thing.
#:
#: One directory per recording, named ``<YYYYMMDD-HHMMSS>-<session-id>`` — the same name as the
#: transcript database's stem in ``data/sessions``, which is how the two are joined. The layout is
#: owned by ``services/recording/layout.py``; nothing else builds a path inside here.
RECORDINGS_DIR: Path = DATA_DIR / "recordings"
#: Where a hand-installed wheel is kept so a later ``uv sync`` that replaces it can be undone
#: without downloading it again. Only the ROCm build of CTranslate2 lives here today; nothing
#: creates the directory, so its absence simply means no wheel has been kept.
WHEELS_DIR: Path = DATA_DIR / "wheels"
LOGS_DIR: Path = REPO_ROOT / "logs"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` and its parents if absent, then return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
