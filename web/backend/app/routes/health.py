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

    credentials: CredentialStore = request.app.state.credentials
    optional = _optional_capabilities()
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
        "modes": _mode_availability(optional),
    }


def _optional_capabilities() -> dict[str, bool]:
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
        # Window capture needs a desktop screen-cast portal and a capture backend. Detecting that
        # properly is Plan 4's first step; until it lands the honest answer is a flat no, which is
        # not the same as the key being absent.
        "window_capture": False,
    }


def _mode_availability(optional: dict[str, bool]) -> dict[str, dict[str, Any]]:
    """Per capture mode: whether it can run, what it is missing, and how to get it."""
    remedies = {
        "audio_device": "Install the audio backend: uv sync --extra audio-device",
        "window_capture": "Window recording is not built yet — see docs/plans/",
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
