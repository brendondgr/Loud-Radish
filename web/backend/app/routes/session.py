"""Session control, audio device selection, and speech-model management.

Thin, as routes should be: validate, call a service, shape the response. Every failure returns the
error envelope with a message that names the remedy, because these are the endpoints a user hits
when something is already going wrong.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..models.session import SessionMetadata
from ..schemas.api import (
    AsrModelsResponse,
    DeviceListResponse,
    LoadModelRequest,
    SelectDeviceRequest,
    SessionPromptRequest,
    SessionResponse,
    SessionStoppedResponse,
    StartSessionRequest,
)
from ..services.asr import available_backends
from ..services.asr.contract import AsrLoadError
from ..services.audio import DeviceEnumerationError, device_support_available, list_devices
from ..services.session import SessionError

router = APIRouter(prefix="/api", tags=["session"])


def _manager(request: Request):  # noqa: ANN201 - returns SessionManager
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503, detail=_error("not-ready", "The pipeline is starting.")
        )
    return manager


def _error(code: str, message: str, severity: str = "warning") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "severity": severity}}


# -- session ---------------------------------------------------------------------------


@router.get("/session", response_model=SessionResponse)
async def get_session(request: Request) -> dict[str, Any]:
    """The current session and pipeline state."""
    return _manager(request).state()


@router.post("/session/start", response_model=SessionResponse)
async def start_session(request: Request, body: StartSessionRequest) -> dict[str, Any]:
    """Begin capture and transcription."""
    manager = _manager(request)
    import uuid

    metadata = SessionMetadata(
        session_id=uuid.uuid4().hex[:12],
        title=body.title,
        venue=body.venue,
        speaker=body.speaker,
    )
    try:
        await manager.start(metadata)
    except SessionError as exc:
        # 409: the request is well-formed, it just conflicts with the current state or environment.
        raise HTTPException(
            status_code=409, detail=_error("session-start-failed", str(exc), "critical")
        ) from exc
    return manager.state()


@router.post("/session/stop", response_model=SessionStoppedResponse)
async def stop_session(request: Request) -> dict[str, Any]:
    """End the session and return its final statistics."""
    manager = _manager(request)
    # Read the id before stopping: afterwards the manager has torn the session down.
    session = manager.state().get("session") or {}
    session_id = session.get("session_id", "")
    try:
        stats = await manager.stop()
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=_error("no-session", str(exc))) from exc
    return {"session_id": session_id, "stats": stats.as_dict()}


# -- audio -----------------------------------------------------------------------------


@router.get("/audio/devices", response_model=DeviceListResponse)
async def get_devices() -> dict[str, Any]:
    """Microphones and loopback devices in one merged list, each tagged with its type.

    Unavailability is reported rather than hidden: an empty list with no explanation looks like a
    broken feature, whereas one naming the install command is actionable.
    """
    supported = device_support_available()
    try:
        devices = list_devices()
    except DeviceEnumerationError as exc:
        raise HTTPException(
            status_code=503, detail=_error("device-enumeration-failed", str(exc), "critical")
        ) from exc

    return {
        "devices": [device.as_dict() for device in devices],
        "device_support": supported,
        "note": (
            ""
            if supported
            else "Live capture needs the optional audio backend: uv sync --extra audio-device"
        ),
    }


@router.post("/audio/device")
async def select_device(request: Request, body: SelectDeviceRequest) -> dict[str, Any]:
    """Choose the capture source. Takes effect when the next session starts."""
    config = request.app.state.config
    changes: dict[str, Any] = {
        "audio.source_type": body.source_type,
        "audio.device_id": body.device_id,
    }
    if body.file_path is not None:
        changes["audio.file_path"] = body.file_path

    hot_swap = config.update(changes)
    return {"applied": True, "hot_swap": str(hot_swap)}


# -- speech recognition ------------------------------------------------------------------


@router.get("/asr/models", response_model=AsrModelsResponse)
async def get_asr_models(request: Request) -> dict[str, Any]:
    """Every backend with the information needed to choose between them (FE §7.2)."""
    manager = _manager(request)
    return {
        "backends": [info.as_dict() for info in available_backends()],
        "current": manager.asr.status(),
    }


@router.post("/asr/load")
async def load_model(request: Request, body: LoadModelRequest) -> dict[str, Any]:
    """Load a model. Progress arrives over the WebSocket as ``asr.progress``."""
    manager = _manager(request)
    config = request.app.state.config

    changes: dict[str, Any] = {"asr.backend": body.backend, "asr.model": body.model}
    if body.device is not None:
        changes["asr.device"] = body.device
    if body.precision is not None:
        changes["asr.precision"] = body.precision
    config.update(changes)

    try:
        # A mid-session swap flushes first, so text is never misattributed to the new model.
        if manager.is_running:
            await manager.swap_model(config.resolve())
        else:
            await manager.asr.load(config.resolve().asr)
    except AsrLoadError as exc:
        raise HTTPException(
            status_code=409, detail=_error("model-load-failed", str(exc), "critical")
        ) from exc
    return manager.asr.status()


@router.post("/asr/unload")
async def unload_model(request: Request) -> dict[str, Any]:
    """Free the model and its device memory — relevant when a local LLM shares the GPU."""
    manager = _manager(request)
    await manager.asr.unload()
    return manager.asr.status()


@router.post("/asr/prompt")
async def set_session_prompt(request: Request, body: SessionPromptRequest) -> dict[str, Any]:
    """Set the biasing prompt. Live: it applies to the next inference pass."""
    manager = _manager(request)
    config = request.app.state.config
    config.update(
        {
            "asr.session_prompt": body.prompt,
            "asr.use_session_prompt": body.use_session_prompt,
            "asr.use_rolling_prompt": body.use_rolling_prompt,
        }
    )
    manager.apply_live_config()
    return {"applied": True, "prompt_length": len(body.prompt)}
