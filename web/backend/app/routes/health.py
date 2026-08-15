"""Liveness and readiness reporting.

Deliberately cheap and dependency-free: this endpoint must answer even when the ASR model failed to
load and every other part of the pipeline is unhappy, because that is exactly when someone reaches
for it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..services.session import modes

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Report that the process is alive, and which optional capabilities are installed."""
    from ..config import CredentialStore
    from ..services.asr.acceleration import detect
    from ..services.capture import detect as detect_capture

    credentials: CredentialStore = request.app.state.credentials
    capture = detect_capture()
    optional = _optional_capabilities(capture.available)
    return {
        "status": "ok",
        "credentials_backend": credentials.backend_name,
        "optional": optional,
        # What GPU acceleration is available, and what is stopping it if it is not. Reported here
        # because the alternative is discovering it when the user presses record.
        "acceleration": detect().as_dict(),
        # Which capture modes this build can actually run, and why not when it cannot. The frontend
        # shows every mode always and disables the unavailable ones with the reason, so a missing
        # capability reads as something to install rather than a feature that does not exist.
        "modes": _mode_availability(optional, capture.reason),
        # Which part of the window-capture stack is missing, when one is. Five distinct verdicts
        # rather than one, because each has a different fix (D-022).
        "capture": capture.as_dict(),
    }


def _optional_capabilities(window_capture: bool = False) -> dict[str, bool]:
    """Which optional dependency groups are present in this environment.

    Reported rather than assumed so a missing model backend shows up here instead of as a confusing
    failure when the user presses record.
    """
    import importlib.util as util

    return {
        "asr_whisper": util.find_spec("faster_whisper") is not None,
        "audio_device": util.find_spec("sounddevice") is not None,
        "vad_silero": util.find_spec("onnxruntime") is not None,
        "credentials": util.find_spec("keyring") is not None,
        # Not a dependency group: window capture needs a portal, GStreamer, and an encoder as
        # well as a Python package, so the answer comes from the probe rather than from an import.
        "window_capture": window_capture,
    }


def _mode_availability(
    optional: dict[str, bool], capture_reason: str = ""
) -> dict[str, dict[str, Any]]:
    """Per capture mode: whether it can run, what it is missing, and how to get it."""
    remedies = {
        "audio_device": "The audio backend is missing. Reinstall with: uv sync",
        # Whatever the probe found, verbatim — it already names the one thing to install.
        "window_capture": capture_reason or "Window recording is not available on this machine.",
    }

    availability: dict[str, dict[str, Any]] = {}
    for mode, required in modes.MODE_REQUIREMENTS.items():
        missing = [name for name in required if not optional.get(name, False)]
        availability[mode] = {
            "available": not missing,
            "missing": missing,
            "reason": remedies.get(missing[0], "") if missing else "",
        }
    return availability
