#!/usr/bin/env python
"""Write the shared contracts into ``web/shared/contracts/``.

Two files, because OpenAPI describes request/response endpoints and cannot express a push channel:

* ``openapi.json``   — generated from the FastAPI app.
* ``ws-events.json`` — hand-authored from the event vocabulary, so the frontend has a machine-
  readable list of what may arrive and, critically, which events append versus replace.

Run this whenever a route or an event changes::

    uv run python scripts/generate_contracts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "web" / "backend"))

from app.config import ConfigStore  # noqa: E402
from app.main import create_app  # noqa: E402
from app.transport.events import ALL_EVENTS, COALESCING_EVENTS, CRITICAL_EVENTS  # noqa: E402

CONTRACTS_DIR = REPO_ROOT / "web" / "shared" / "contracts"

#: What each event means to a client. The append/replace distinction is the most important part of
#: the whole contract and is stated here in machine-readable form so it cannot be misread.
EVENT_NOTES: dict[str, dict[str, str]] = {
    "session.started": {"action": "set", "note": "Capture began. Carries a config snapshot."},
    "session.stopped": {"action": "set", "note": "Capture ended. Carries final statistics."},
    "transcript.committed": {
        "action": "append",
        "note": "Append permanently. Never modify an existing entry. Order by id, not arrival.",
    },
    "transcript.hypothesis": {
        "action": "replace",
        "note": (
            "Replace the tentative tail wholly. NOT a list entry — treating it as one duplicates "
            "text on screen. An empty text means clear the tail."
        ),
    },
    "audio.level": {"action": "replace", "note": "Drives the input meter."},
    "vad.state": {"action": "replace", "note": "Drives the speaking indicator."},
    "status": {
        "action": "replace",
        "note": "Health telemetry. rtf below 1.0 must show as a problem.",
    },
    "asr.progress": {"action": "replace", "note": "Model load progress, with a named state."},
    "summary.added": {"action": "append", "note": "A new entry in the running outline."},
    "glossary.added": {"action": "append", "note": "A newly identified term."},
    "chat.delta": {"action": "append", "note": "One fragment of a streaming answer."},
    "chat.done": {"action": "set", "note": "The answer is complete. Carries usage and citations."},
    "error": {"action": "notify", "note": "severity is info | warning | critical. Never a modal."},
}


def build_ws_contract() -> dict[str, object]:
    """Describe the WebSocket channel."""
    return {
        "version": 1,
        "envelope": {"event": "string", "data": "object"},
        "client_frames": {
            "hello": {
                "since": "last received segment id, or null for a fresh client",
                "note": "Sent on every connect. The server replays everything after `since`.",
            },
            "ping": {"note": "Keepalive. Answered with a `pong` event."},
        },
        "reconnection": [
            "Detect the disconnect and show it. DO NOT clear the transcript.",
            "Reconnect with exponential backoff.",
            "Send hello with the last segment id received.",
            "Append the replayed segments.",
            "Ignore any segment id already held — this makes the replay idempotent.",
        ],
        "events": {
            name: {
                **EVENT_NOTES.get(name, {}),
                "critical": name in CRITICAL_EVENTS,
                "coalescing": name in COALESCING_EVENTS,
            }
            for name in ALL_EVENTS
        },
    }


def main() -> int:
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

    app = create_app(config=ConfigStore(config_path=REPO_ROOT / "data" / "contract-scratch.json"))
    openapi_path = CONTRACTS_DIR / "openapi.json"
    openapi_path.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")

    ws_path = CONTRACTS_DIR / "ws-events.json"
    ws_path.write_text(json.dumps(build_ws_contract(), indent=2) + "\n", encoding="utf-8")

    print(f"{openapi_path.relative_to(REPO_ROOT)}  ({len(app.routes)} routes)")
    print(f"{ws_path.relative_to(REPO_ROOT)}  ({len(ALL_EVENTS)} events)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
