"""What every script does before anything else: find the backend, and undo what `uv run` did.

**Two lines that every script used to carry, and one that only `app.py` did.** The two put
`web/backend` on the path so `from app...` resolves. The one puts the ROCm build of CTranslate2
back after `uv run` has replaced it — because a bare `uv run` synchronises the environment against
a lockfile that says PyPI *before Python starts*, and the PyPI wheel is the CPU/CUDA build. On an
AMD machine that means running **any** script with `uv run` breaks the server's next model load,
and running it again through `uv run` re-breaks what a manual reinstall just fixed. Encountered
while testing dictation from a script (D-064).

`app.py` has repaired that on its own launch since D-038's predecessor. Nothing else did, and the
checklist entry asked for the repair to live somewhere every entry point passes through. This is
that place: a script's first import, in front of everything that might import CTranslate2.

Usage, at the top of a script, after the standard-library imports::

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _bootstrap import REPO_ROOT, prepare  # noqa: E402

    prepare()

The first line is what lets a test load the script by file path, from a working directory that is
not `scripts/`. The repair is guarded the way the launcher's is: a script must not fail to run
because a repair did, and the repair itself does nothing on a machine with no AMD GPU, or when the
installed build already is the kept one. `LOUD_RADISH_NO_GPU_REPAIR=1` switches it off, which the
test suite sets so that importing a script can never reinstall a wheel.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "web" / "backend"


def prepare(*, repair_gpu: bool = True) -> str | None:
    """Put the backend on ``sys.path`` and the ROCm wheel back. Returns the repair's message.

    Idempotent: calling it twice adds nothing twice, and the repair's own guard makes the second
    call a no-op once the first has put the wheel back.
    """
    backend = str(BACKEND_ROOT)
    if backend not in sys.path:
        sys.path.insert(0, backend)
    if not repair_gpu:
        return None
    return repair_gpu_build()


def repair_gpu_build() -> str | None:
    """The same repair ``app.py`` performs on launch, and just as unwilling to be fatal."""
    try:
        from app.services.asr.acceleration import repair_kept_wheel

        message = repair_kept_wheel()
    except Exception:  # noqa: BLE001 - a repair must not become a failure to run
        return None
    if message:
        print(message, file=sys.stderr)
    return message


__all__ = ["BACKEND_ROOT", "REPO_ROOT", "prepare", "repair_gpu_build"]
