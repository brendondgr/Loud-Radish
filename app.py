#!/usr/bin/env python
"""Run the Live Seminar Transcriber.

One command starts everything: the API, the WebSocket event stream, and the browser interface, all
from a single process.

    uv run app.py

Then open http://127.0.0.1:8395.

**Running it again takes the port back.** Starting this twice is the ordinary case — you run it,
leave it, and come back without remembering the first is still up — so the second run stops the
first rather than refusing. It will only ever stop a server it has positively identified as this
application; anything else on the port is left alone and reported. ``--no-takeover`` restores the
old refusal.

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

#: Every capability the application has, and the module that provides it (D-023).
#:
#: **None of these are optional any more.** They are all ordinary dependencies, so `uv run app.py`
#: installs the lot and this list is a *health check* rather than a menu: anything missing here
#: means a broken or partial install, not a decision someone made.
CAPABILITIES: dict[str, str] = {
    "faster_whisper": "Real transcription",
    "sounddevice": "Microphone and loopback capture",
    "onnxruntime": "Silero voice detection",
    "keyring": "OS credential store for API keys",
    "jeepney": "Window capture (screen-sharing portal)",
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


def _capabilities() -> list[tuple[str, str, bool]]:
    """Which capabilities are present, without importing any of them."""
    return [
        (module, description, importlib.util.find_spec(module) is not None)
        for module, description in CAPABILITIES.items()
    ]


def _acceleration_lines() -> list[str]:
    """Where GPU acceleration stands, and what would fix it.

    Reported at startup because every way this can be wrong — no ROCm build, a missing runtime
    library, a GPU the kernel cannot see — surfaces identically at the moment the user presses
    record, with nothing to say which of them applies.

    Import is deferred and guarded: this is a launcher, and a banner must never be the reason the
    application will not start.
    """
    try:
        from app.services.asr.acceleration import describe_lines

        lines = describe_lines()
    except Exception:  # noqa: BLE001 - a diagnostic must not become a failure
        return []

    if not lines:
        return []
    return ["", f"    {lines[0]}"] + [f"    {line}" for line in lines[1:]]


def _repair_gpu_build() -> str | None:
    """Put the ROCm build of CTranslate2 back when the launch itself replaced it.

    ``uv run app.py`` synchronises the environment against the lockfile before this file executes,
    and the lockfile names PyPI — which ships a CPU-and-CUDA build. On an AMD machine that means the
    act of starting the application is what breaks its GPU support, every time, and the resulting
    error at record time says *CUDA* on a machine that has no NVIDIA hardware.

    Called before anything imports CTranslate2, so the reinstalled build is the one that gets
    loaded. Guarded like every other diagnostic here: a launcher must not fail because a repair did.
    """
    try:
        from app.services.asr.acceleration import repair_kept_wheel

        return repair_kept_wheel()
    except Exception:  # noqa: BLE001 - a repair must not become a failure to start
        return None


def _port_is_free(host: str, port: int) -> bool:
    """Whether ``port`` can be bound, so a clash is reported clearly rather than as a traceback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


# -- taking the port back ------------------------------------------------------------------
#
# Starting this application twice is the ordinary case, not the exceptional one: you run it, leave
# it, come back, and run it again without remembering the first is still up. Refusing with "port in
# use" made that a chore, so the second run takes the port from the first.
#
# **It will only ever stop a server it has identified as this application.** Something else on 8395
# is refused exactly as before, because a launcher that kills whatever is in its way is a launcher
# that will one day kill a database. Identification is two independent checks that must both pass:
# the port answers `/api/health` the way this application does, *and* the process holding it has
# this repository's launcher or its uvicorn entry point on its command line.


def _identifies_as_ours(host: str, port: int, timeout: float = 1.5) -> bool:
    """Whether whatever is on ``port`` answers ``/api/health`` the way this application does."""
    import json
    import urllib.error
    import urllib.request

    url = f"http://{host}:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - loopback
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False

    # Shape, not just a 200. Any web server can return JSON; only this one returns these keys.
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and isinstance(payload.get("optional"), dict)
        and isinstance(payload.get("modes"), dict)
    )


def _pids_on_port(port: int) -> list[int]:
    """Every process holding ``port``, found through ``/proc``.

    Linux-only and deliberately dependency-free: ``psutil`` for one lookup in a launcher is not a
    trade worth making, and ``lsof``/``ss`` are not guaranteed installed. Returns an empty list on
    any other platform, which downgrades takeover to the old refusal rather than breaking it.

    More than one pid is normal. Under ``--reload`` uvicorn's parent owns the listening socket and
    its child inherits, so both appear — and stopping only one leaves the other to rebind.
    """
    if not sys.platform.startswith("linux"):
        return []

    inodes = _socket_inodes_for_port(port)
    if not inodes:
        return []

    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for descriptor in (entry / "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    pids.append(int(entry.name))
                    break
        except OSError:
            # A process that exited between the listing and the read, or one we do not own.
            continue
    return pids


def _socket_inodes_for_port(port: int) -> set[str]:
    """Inodes of listening sockets bound to ``port``, from ``/proc/net/tcp{,6}``."""
    wanted = f"{port:04X}"
    inodes: set[str] = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = Path("/proc/net", name).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10:
                continue
            local = fields[1]
            # "0A" is TCP_LISTEN. A connected socket to the same port is somebody's client, not the
            # server, and killing its owner would be killing a browser.
            if local.endswith(f":{wanted}") and fields[3] == "0A":
                inodes.add(fields[9])
    return inodes


def _process_looks_like_this_app(pid: int) -> bool:
    """Whether ``pid``'s command line is this launcher or the uvicorn entry point it starts."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except OSError:
        return False
    parts = cmdline.split("\0")
    return any("app.py" in part or "app.main:app" in part for part in parts)


def _take_over_port(host: str, port: int) -> bool:
    """Stop this application's own server on ``port``. Returns whether the port came free.

    Asks politely first. ``SIGTERM`` lets the server run its shutdown — which flushes the last
    words of a session in progress and closes the transcript database cleanly. ``SIGKILL`` is a last
    resort after five seconds, and it costs the tail of whatever was recording.
    """
    import signal
    import time

    if not _identifies_as_ours(host, port):
        return False

    pids = [pid for pid in _pids_on_port(port) if _process_looks_like_this_app(pid)]
    if not pids:
        return False

    print(f"  Port {port} is already serving this application — taking it over.")
    print(f"  Stopping {'process' if len(pids) == 1 else 'processes'} {', '.join(map(str, pids))}…")
    sys.stdout.flush()

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            continue

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if _port_is_free(host, port):
            return True
        time.sleep(0.1)

    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            continue

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if _port_is_free(host, port):
            print("  It did not stop on request and was killed.")
            return True
        time.sleep(0.1)
    return False


def _print_banner(host: str, port: int, reload: bool, repaired: str | None = None) -> None:
    """Say what is running, where, and what it can and cannot do."""
    url = f"http://{host}:{port}"
    print()
    print("  Live Seminar Transcriber")
    print(f"  {url}")
    print()

    # Ahead of the capability list, because the repair changes what that list and the acceleration
    # report below it will say.
    if repaired:
        print(f"    {repaired}")
        print()

    capabilities = _capabilities()
    missing = [description for _, description, present in capabilities if not present]

    for _, description, present in capabilities:
        print(f"    {'✓' if present else '✗'} {description}")

    # Anything absent now means a broken install rather than a choice, so it is reported as a
    # problem with a fix, not as an upsell.
    if missing:
        print()
        print("    Some of the above are missing, which means the install is incomplete.")
        print("    Put them back with:  uv sync")

    for line in _acceleration_lines():
        print(line)

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
        "--no-takeover",
        action="store_true",
        help="Refuse if the port is busy, instead of stopping an older instance of this app.",
    )
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
        taken_over = not args.no_takeover and _take_over_port(args.host, args.port)
        if not taken_over:
            # Either something else owns the port, or takeover was declined, or the old server
            # would not die. All three want the same message: this is not ours to reclaim.
            hint = (
                "It is not this application, so it has been left alone."
                if not args.no_takeover
                else "Takeover is off (--no-takeover)."
            )
            print(
                f"Port {args.port} is already in use on {args.host}.\n"
                f"{hint}\n"
                f"Either stop what is using it, or choose another:\n\n"
                f"    uv run app.py --port {args.port + 1}\n",
                file=sys.stderr,
            )
            return 1

    # Before anything imports CTranslate2 — including the acceleration report in the banner — so
    # the build this restores is the one the server actually loads.
    _print_banner(args.host, args.port, args.reload, _repair_gpu_build())

    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}")

    try:
        return _serve(args.host, args.port, args.reload, args.log_level)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
