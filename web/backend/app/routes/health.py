"""Liveness and readiness reporting.

Deliberately cheap and dependency-free: this endpoint must answer even when the ASR model failed to
load and every other part of the pipeline is unhappy, because that is exactly when someone reaches
for it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Report that the process is alive, and which optional capabilities are installed."""
    from ..config import CredentialStore

    credentials: CredentialStore = request.app.state.credentials
    return {
        "status": "ok",
        "credentials_backend": credentials.backend_name,
        "optional": _optional_capabilities(),
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
    }
