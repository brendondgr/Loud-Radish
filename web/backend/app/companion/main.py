"""The companion process: a tray icon and a set of shortcuts, and nothing else (Plan 5).

**A web page cannot do any of this.** A page in a browser tab cannot register a system-wide hotkey,
cannot place an icon in the tray, and does not exist when no browser is running. So the application
grows a second, small process that owns those two things and talks to the server over the same
loopback HTTP the browser uses.

Keeping it this thin is what stops Plan 5 becoming a desktop-application rewrite. It is a **remote
control**: the server stays the only thing that records, and the companion can be killed and
restarted at any moment without the server noticing — which is the property that makes a background
helper safe to ship at all.

It *polls* rather than consuming the WebSocket, for the same reason. A poller is stateless and
disposable; a second socket consumer would need reconnection logic, replay handling, and a view of
transcript events it has no use for.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import urllib.error
import urllib.request
from typing import Any, Final

from .. import branding
from .animation import FrameClock, Snapshot
from .tray import TrayIcon

logger = logging.getLogger(__name__)

DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8395

#: How often to ask the server what it is doing. Twice a second is responsive enough that the tray
#: does not lag a keypress, and cheap enough to be invisible next to inference.
POLL_INTERVAL_S: Final = 0.5

#: Short: an unanswered request means the server is down, which is a state to *show* rather than
#: wait on.
TIMEOUT_S: Final = 2.0


def read_state(base: str) -> Snapshot:
    """Ask the server what it is doing. Never raises — unreachable is an answer."""
    try:
        request = urllib.request.Request(f"{base}/api/session")  # noqa: S310 - loopback
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return Snapshot(reachable=False)

    session = payload.get("session") or {}
    job = payload.get("transcription") or {}

    if payload.get("running"):
        state = "recording"
    elif job.get("state") == "running":
        state = "processing"
    else:
        state = "idle"

    return Snapshot(
        reachable=True,
        mode=session.get("mode", "live"),
        state=state,
        # The polish pass runs during a session and has no run state of its own (D-018), so it is
        # carried separately rather than folded into `state`.
        running_pass=bool(payload.get("polishing")),
    )


def _repo_root() -> str:
    """Where the checkout is, from the environment the launcher sets."""
    return os.environ.get(branding.env_var("ROOT")) or os.environ.get(
        branding.legacy_env_var("ROOT"), "."
    )


class _Shortcuts:
    """The shortcut settings as attributes, which is what `register_all` reads.

    The companion has no Pydantic model of its own — it deliberately holds no schema, because a
    second definition of the settings is a second thing to keep in step with the backend.
    """

    def __init__(self, values: dict[str, Any]) -> None:
        self.__dict__.update(values)

    def __getattr__(self, name: str) -> Any:
        return ""


class Companion:
    """Polls the server, drives the frame clock, and answers menu activations."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        hold_still: bool = False,
        tray: bool = True,
        repo_root: str | None = None,
    ) -> None:
        self.base = f"http://{host}:{port}"
        self.listening = True
        self.repo_root = repo_root or _repo_root()
        #: What `bind_shortcuts` last reported, for the menu and the settings window.
        self.shortcuts: list = []
        self.clock = FrameClock(self._on_frame, hold_still=hold_still)
        self._stop = threading.Event()
        self._latest_svg = ""
        # The tray is optional at construction so the state tests, which drive the clock directly,
        # never touch a session bus. `--no-tray` is the same switch from the command line.
        self.tray = (
            TrayIcon(on_activate=self.activate, listening=lambda: self.listening) if tray else None
        )

    @property
    def latest_svg(self) -> str:
        """The most recent frame, for whatever is drawing it."""
        return self._latest_svg

    def _on_frame(self, frame, visual) -> None:  # noqa: ANN001
        self._latest_svg = frame.svg
        if self.tray is not None:
            self.tray.update(frame, visual)

    # -- the loop --------------------------------------------------------------------

    def poll_once(self) -> Snapshot:
        snapshot = read_state(self.base)
        self.clock.update(snapshot)
        # The menu is rebuilt from this, and only when it has actually changed — a poll every half
        # second that signalled `LayoutUpdated` each time would have the host re-reading a menu
        # nobody has opened.
        if self.tray is not None:
            self.tray.set_snapshot(snapshot)
        return snapshot

    def run(self) -> int:
        if self.tray is not None and not self.tray.start():
            logger.info("Running without a tray icon; shortcuts and the menu are unaffected")
        self.bind_shortcuts()
        self.clock.start()
        logger.info("Companion watching %s", self.base)
        try:
            while not self._stop.wait(POLL_INTERVAL_S):
                self.poll_once()
        except KeyboardInterrupt:
            pass
        finally:
            self.clock.stop()
            if self.tray is not None:
                self.tray.stop()
        return 0

    def bind_shortcuts(self) -> list:
        """Install the desktop entries and bind the keys. Reports, and never raises.

        **`register_all` had no call site at all until now**, so the five configured shortcuts had
        never been registered with anything and the "Listening for shortcuts" checkmark toggled a
        flag nothing read. The bindings live in the desktop's own configuration once made, so this
        is a reconciliation on start rather than something the keys depend on staying alive for.
        """
        from .shortcuts import describe, register_all

        try:
            config = self._read_config().get("shortcuts") or {}
        except Exception as exc:  # noqa: BLE001
            logger.info("Could not read the shortcut settings: %s", exc)
            return []

        results = register_all(_Shortcuts(config), self.repo_root)
        logger.info("Shortcuts:\n%s", describe(results, self.repo_root))
        self.shortcuts = results
        return results

    def _read_config(self) -> dict[str, Any]:
        """The server's resolved configuration. Unreachable is an empty answer, not an error."""
        request = urllib.request.Request(f"{self.base}/api/config")  # noqa: S310 - loopback
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return payload.get("config") or {}

    def stop(self) -> None:
        self._stop.set()

    # -- what the menu does ------------------------------------------------------------

    def activate(self, item_id: str) -> str:
        """Handle one menu activation. Returns a one-line result, for logs and tests."""
        import subprocess

        ctl = [sys.executable, f"{self.repo_root}/{branding.CONTROL_SCRIPT}"]

        if item_id == "listening":
            self.listening = not self.listening
            return f"shortcuts {'armed' if self.listening else 'disarmed'}"
        if item_id == "quit":
            self.stop()
            return "quitting"
        if item_id in ("open", "settings"):
            import webbrowser

            webbrowser.open(self.base)
            return "opened the interface"

        commands = {
            "stop": ["stop"],
            "start-live": ["start", "--mode", "live"],
            "start-recorded": ["start", "--mode", "recorded"],
            "arm-window": ["arm", "--mode", "window"],
        }
        if item_id not in commands:
            return f"unknown item: {item_id}"

        try:
            subprocess.run([*ctl, *commands[item_id]], check=False, timeout=130)  # noqa: S603
        except (OSError, subprocess.SubprocessError) as exc:
            return f"failed: {exc}"
        return " ".join(commands[item_id])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{branding.APP_SLUG}-companion", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("API_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("API_PORT", DEFAULT_PORT)))
    parser.add_argument(
        "--hold-still",
        action="store_true",
        help="Do not animate. There is no prefers-reduced-motion in a tray, so this is it.",
    )
    parser.add_argument(
        "--no-tray",
        action="store_true",
        help="Do not place an icon in the system tray. Shortcuts still work.",
    )
    parser.add_argument("--once", action="store_true", help="Poll once and print the state.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    companion = Companion(
        host=args.host,
        port=args.port,
        hold_still=args.hold_still,
        tray=not (args.no_tray or args.once),
    )

    if args.once:
        snapshot = companion.poll_once()
        visual = snapshot.visual()
        print(f"{visual.name}  ({visual.description})")
        return 0

    return companion.run()


if __name__ == "__main__":
    raise SystemExit(main())
