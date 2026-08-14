#!/usr/bin/env python
"""Run the Live Seminar Transcriber.

One command starts everything: the API, the WebSocket event stream, and the browser interface, all
from a single process.

    uv run python app.py

Then open http://127.0.0.1:8395.

This file is a launcher, not application code — it holds no logic of its own. Everything it starts
lives under ``web/``, per the layout rule in ``docs/structure.md``.

**A note on the name.** There is also a package at ``web/backend/app/``, and ``uvicorn`` imports it
as ``app.main``. If the repository root came first on ``sys.path``, that import would find *this
file* instead and fail with "'app' is not a package". :func:`_prepare_import_path` guarantees the
ordering, and ``--reload`` is verified against it.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import sys
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = REPO_ROOT / "web" / "backend"

#: The application's port. Not a common default, so it is unlikely to collide with whatever else is
#: already running on the machine — including the local model servers this application talks to
#: (Ollama on 11434, LM Studio on 1234, llama.cpp on 8080).
DEFAULT_PORT = 8395

#: Loopback, deliberately. This application is single-user and has no authentication, so binding it
#: to a network interface would publish an unauthenticated transcript of a private room.
DEFAULT_HOST = "127.0.0.1"

#: Optional dependency groups, and what each one adds. Reported at startup so a missing capability
#: is visible now rather than as a confusing failure when the user presses record.
OPTIONAL_GROUPS: dict[str, tuple[str, str]] = {
    "faster_whisper": ("Real transcription", "uv sync --extra asr-whisper"),
    "sounddevice": ("Microphone and loopback capture", "uv sync --extra audio-device"),
    "onnxruntime": ("Silero voice-activity detection", "uv sync --extra vad-silero"),
    "keyring": ("OS credential store for API keys", "uv sync --extra credentials"),
}


def _prepare_import_path() -> None:
    """Put ``web/backend`` ahead of the repository root on ``sys.path``.

    Running this file makes the repository root ``sys.path[0]``, where ``app`` resolves to this
    module. Inserting the backend directory in front means ``app`` resolves to the package instead.
    """
    backend = str(BACKEND_DIR)
    if backend in sys.path:
        sys.path.remove(backend)
    sys.path.insert(0, backend)


def _load_env_file(path: Path) -> int:
    """Load ``KEY=value`` pairs from a ``.env`` file, without overriding the real environment.

    Deliberately minimal, and deliberately not a dependency: the file is ours, the format is two
    lines of parsing, and an explicit environment variable must always win over a file.
    """
    if not path.is_file():
        return 0

    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")
            loaded += 1
    return loaded


def _optional_capabilities() -> list[tuple[str, str, bool, str]]:
    """Which optional groups are installed, without importing any of them."""
    return [
        (module, description, importlib.util.find_spec(module) is not None, install)
        for module, (description, install) in OPTIONAL_GROUPS.items()
    ]


def _port_is_free(host: str, port: int) -> bool:
    """Whether ``port`` can be bound, so a clash is reported clearly rather than as a traceback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _print_banner(host: str, port: int, reload: bool) -> None:
    """Say what is running, where, and what it can and cannot do."""
    url = f"http://{host}:{port}"
    print()
    print("  Live Seminar Transcriber")
    print(f"  {url}")
    print()

    for _, description, present, install in _optional_capabilities():
        mark = "✓" if present else "·"
        suffix = "" if present else f"   ({install})"
        print(f"    {mark} {description}{suffix}")

    if not any(present for _, _, present, _ in _optional_capabilities()):
        print()
        print("    Nothing optional is installed, so the scripted mock backend will run.")
        print("    That transcribes placeholder text — enough to see the interface work.")

    if host not in ("127.0.0.1", "localhost"):
        print()
        print(f"    ! Bound to {host}, not loopback. This application has no authentication,")
        print("      so anything that can reach this port can read the transcript.")

    if reload:
        print()
        print("    Auto-reload is on. Editing a file under web/ restarts the server.")

    print()
    print("  Ctrl+C to stop.")
    print()

    # Flushed explicitly: stdout is block-buffered when redirected to a file or a pipe, so the
    # banner would otherwise sit in the buffer until the process exits — precisely when it has
    # stopped being useful.
    sys.stdout.flush()


def _serve(host: str, port: int, reload: bool, log_level: str) -> int:
    """Start the server. Returns a process exit code."""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        # Watch only the application, so an edit to a test or a doc does not restart a live
        # session mid-talk.
        reload_dirs=[str(BACKEND_DIR / "app")] if reload else None,
        log_level=log_level,
        # Named so uvicorn's own worker resolves the package the same way this launcher does.
        app_dir=str(BACKEND_DIR),
        access_log=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.py",
        description="Run the Live Seminar Transcriber (API, WebSocket, and interface).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("API_PORT", DEFAULT_PORT)),
        help=f"Port to serve on (default: {DEFAULT_PORT}, or API_PORT).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("API_HOST", DEFAULT_HOST),
        help=f"Interface to bind (default: {DEFAULT_HOST}). Leave this on loopback.",
    )
    parser.add_argument(
        "--reload", action="store_true", help="Restart when application code changes."
    )
    parser.add_argument("--open", action="store_true", help="Open the interface in a browser.")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "info"),
        choices=["critical", "error", "warning", "info", "debug"],
    )
    args = parser.parse_args(argv)

    _load_env_file(REPO_ROOT / ".env")
    _prepare_import_path()

    # Created up front so the first session does not fail on a missing directory.
    for directory in (REPO_ROOT / "data", REPO_ROOT / "logs"):
        directory.mkdir(parents=True, exist_ok=True)

    if not BACKEND_DIR.is_dir():
        print(f"Cannot find the backend at {BACKEND_DIR}.", file=sys.stderr)
        print("Run this from the repository root.", file=sys.stderr)
        return 2

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("Dependencies are not installed. Run:\n\n    uv sync\n", file=sys.stderr)
        return 2

    if not _port_is_free(args.host, args.port):
        print(
            f"Port {args.port} is already in use on {args.host}.\n"
            f"Either stop what is using it, or choose another:\n\n"
            f"    uv run python app.py --port {args.port + 1}\n",
            file=sys.stderr,
        )
        return 1

    _print_banner(args.host, args.port, args.reload)

    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}")

    try:
        return _serve(args.host, args.port, args.reload, args.log_level)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
