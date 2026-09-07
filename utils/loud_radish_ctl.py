#!/usr/bin/env python
"""Drive Loud Radish from outside the browser (Plan 5).

    uv run utils/loud_radish_ctl.py toggle
    uv run utils/loud_radish_ctl.py start --mode recorded
    uv run utils/loud_radish_ctl.py status

Run by path rather than installed as a console script. Console scripts would mean turning this
repository into a packaged project with a build backend, and `uv run <path>` already works with no
setup at all — which is the same reasoning that makes `uv run app.py` the way to start the server.

This exists first in Plan 5 because everything else there is a way of invoking it: a tray menu item,
a global shortcut, a keyboard binding set by hand in System Settings. It is also useful on its own —
bind it to a key in your desktop's own shortcut editor and you have working global keybinds with no
companion process at all, which makes the companion an improvement rather than a prerequisite.

Dependency-free on purpose: `urllib` from the standard library, so it starts fast enough to sit
behind a keypress and cannot fail because an import went wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_PORT = 8395
DEFAULT_HOST = "127.0.0.1"

#: Short, because this sits behind a keypress. A server that has not answered in this long is a
#: server worth reporting as unreachable rather than waiting on.
TIMEOUT_S = 5.0

#: Longer for the calls that actually do something: starting a session loads a model.
ACTION_TIMEOUT_S = 120.0


class ControlError(RuntimeError):
    """The server could not be reached, or refused."""


def _base(args: argparse.Namespace) -> str:
    return f"http://{args.host}:{args.port}"


def _request(url: str, payload: dict | None = None, timeout: float = TIMEOUT_S) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 - loopback, fixed scheme
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body)["detail"]["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = body.strip() or f"HTTP {exc.code}"
        raise ControlError(detail) from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ControlError("Loud Radish is not running. Start it with: uv run app.py") from exc


def cmd_status(args: argparse.Namespace) -> int:
    state = _request(f"{_base(args)}/api/session")
    session = state.get("session") or {}
    job = state.get("transcription")

    if state.get("running"):
        print(f"recording · {session.get('mode', 'live')} mode")
    elif job and job.get("state") == "running":
        print(f"transcribing · {round(job.get('progress', 0) * 100)}%")
    else:
        print("idle")
    return 0


def cmd_toggle(args: argparse.Namespace) -> int:
    state = _request(
        f"{_base(args)}/api/session/toggle", {"mode": args.mode}, timeout=ACTION_TIMEOUT_S
    )
    print("recording" if state.get("running") else "stopped")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    state = _request(
        f"{_base(args)}/api/session/start", {"mode": args.mode}, timeout=ACTION_TIMEOUT_S
    )
    print(f"recording · {(state.get('session') or {}).get('mode', args.mode)} mode")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    _request(f"{_base(args)}/api/session/stop", {}, timeout=ACTION_TIMEOUT_S)
    print("stopped")
    return 0


def cmd_arm(args: argparse.Namespace) -> int:
    """Open the interface with the pre-flight sheet already up.

    Video and window captures need options answering *before* anything is captured, so a keystroke
    cannot simply start one. Rather than build a second native dialog that would have to be kept in
    step with the browser's, this raises the browser at a URL the page understands.
    """
    import webbrowser

    url = f"{_base(args)}/?arm={args.mode}"
    webbrowser.open(url)
    print(f"opened {url}")
    return 0


def cmd_dictate(args: argparse.Namespace) -> int:
    """Start a dictation, or finish the one already running.

    One key for both halves, because that is how a push-to-talk key is used: press it, speak, press
    it again. A second key to stop would mean remembering two, and getting it wrong mid-sentence
    means the words are lost rather than merely delayed.
    """
    state = _request(f"{_base(args)}/api/dictation/toggle", {}, timeout=ACTION_TIMEOUT_S)
    if state.get("recording"):
        print("dictating…")
    else:
        print(state.get("summary") or "finished")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loud-radish-ctl", description=__doc__)
    parser.add_argument("--host", default=os.environ.get("API_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("API_PORT", DEFAULT_PORT)))

    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler, needs_mode in (
        ("status", cmd_status, False),
        ("toggle", cmd_toggle, True),
        ("start", cmd_start, True),
        ("stop", cmd_stop, False),
        ("arm", cmd_arm, True),
        ("dictate", cmd_dictate, False),
    ):
        command = sub.add_parser(name, help=handler.__doc__ or name)
        command.set_defaults(handler=handler)
        if needs_mode:
            command.add_argument("--mode", default="live", choices=["live", "recorded", "window"])

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ControlError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
