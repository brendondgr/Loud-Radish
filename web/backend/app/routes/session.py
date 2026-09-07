"""Session control, audio device selection, and speech-model management.

Thin, as routes should be: validate, call a service, shape the response. Every failure returns the
error envelope with a message that names the remedy, because these are the endpoints a user hits
when something is already going wrong.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

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
from ..services.audio import (
    DeviceEnumerationError,
    device_support_available,
    library,
    list_devices,
    probe,
)
from ..services.audio.probe import probe_device
from ..services.session import CaptureOptions, SessionError, modes

router = APIRouter(prefix="/api", tags=["session"])

#: Bound once rather than in the signature default, where ruff's B008 correctly objects to a call
#: evaluated at import time.
UPLOADED_FILE = File(...)


def _manager(request: Request):  # noqa: ANN201 - returns SessionManager
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503, detail=_error("not-ready", "The pipeline is starting.")
        )
    return manager


def _error(code: str, message: str, severity: str = "warning") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "severity": severity}}


def _capture_options(body: StartSessionRequest) -> CaptureOptions | None:
    """Translate the request's options into the pipeline's own vocabulary.

    Two types for three booleans is deliberate: `schemas/` is the HTTP validation boundary and
    `services/` must not import it, or the pipeline ends up depending on the shape of a request.
    """
    if body.mode != modes.WINDOW:
        return None
    if body.options is None:
        return CaptureOptions()
    return CaptureOptions(
        live_transcription=body.options.live_transcription,
        post_transcription=body.options.post_transcription,
        video=body.options.video,
        audio_source=body.options.audio_source,
    )


# -- session ---------------------------------------------------------------------------


@router.get("/session", response_model=SessionResponse)
async def get_session(request: Request) -> dict[str, Any]:
    """The current session and pipeline state."""
    return _manager(request).state()


#: Capture modes whose pipeline wiring has landed. The others are accepted by the schema — the
#: vocabulary is fixed (D-020) and the frontend offers all three — but refused here until their plan
#: is implemented, so an unfinished mode gives a named error rather than a session that starts and
#: records nothing. Each plan deletes its own entry from this set.
_UNIMPLEMENTED_MODES: dict[str, str] = {}


@router.post("/session/start", response_model=SessionResponse)
async def start_session(request: Request, body: StartSessionRequest) -> dict[str, Any]:
    """Begin capture and transcription in the requested capture mode."""
    manager = _manager(request)
    import uuid

    # Checked before the "not built yet" refusal, so a combination that can never work is named as
    # such rather than reported as a missing feature. The client refuses this too; that check is a
    # courtesy and this one is the rule.
    if body.options is not None and body.options.records_nothing:
        raise HTTPException(
            status_code=422,
            detail=_error(
                "records-nothing",
                "With live transcription, post-processing, and video all off, "
                "there would be nothing to record.",
            ),
        )

    if body.mode in _UNIMPLEMENTED_MODES:
        # 501 rather than 409: the request is valid and the state is fine — this build simply does
        # not have the feature, which is a different problem with a different remedy.
        raise HTTPException(
            status_code=501,
            detail=_error("mode-unavailable", _UNIMPLEMENTED_MODES[body.mode]),
        )

    metadata = SessionMetadata(
        session_id=uuid.uuid4().hex[:12],
        title=body.title,
        venue=body.venue,
        speaker=body.speaker,
        mode=body.mode,
    )
    try:
        await manager.start(metadata, options=_capture_options(body))
    except SessionError as exc:
        # 409: the request is well-formed, it just conflicts with the current state or environment.
        raise HTTPException(
            status_code=409, detail=_error("session-start-failed", str(exc), "critical")
        ) from exc
    return manager.state()


@router.post("/session/toggle", response_model=SessionResponse)
async def toggle_session(request: Request, body: StartSessionRequest) -> dict[str, Any]:
    """Start if idle, stop if running (D-023's companion, and Plan 5's whole control surface).

    **One endpoint rather than two** because a keystroke has no way to know the current state, and
    a round trip to find out is a race: press the key twice quickly and the second request reads a
    state the first has already changed. Deciding it server-side, where the lock is, removes the
    race rather than narrowing it.
    """
    manager = _manager(request)
    if manager.is_running:
        try:
            await manager.stop()
        except SessionError as exc:
            raise HTTPException(status_code=409, detail=_error("no-session", str(exc))) from exc
        return manager.state()

    return await start_session(request, body)


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


@router.post("/session/pause", response_model=SessionResponse)
async def pause_session(request: Request) -> dict[str, Any]:
    """Hold the capture. The recording stops growing and the clock stops with it (D-044).

    Idempotent: pausing a session that is already held is not an error, because a second click on
    a control whose label has not repainted yet is a user's mistake to make and not worth a banner.
    """
    manager = _manager(request)
    try:
        manager.pause()
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=_error("no-session", str(exc))) from exc
    return manager.state()


@router.post("/session/resume", response_model=SessionResponse)
async def resume_session(request: Request) -> dict[str, Any]:
    """Continue a held capture, in the same session, the same file and the same store."""
    manager = _manager(request)
    try:
        manager.resume()
    except SessionError as exc:
        raise HTTPException(status_code=409, detail=_error("no-session", str(exc))) from exc
    return manager.state()


@router.post("/session/cancel", response_model=SessionStoppedResponse)
async def cancel_session(request: Request) -> dict[str, Any]:
    """End the session and transcribe nothing. Every artefact it produced is kept (D-044).

    The difference from `/session/stop` is exactly one thing: the post-capture pass does not run.
    The audio, the video and any committed segments stay where they are, and the recording remains
    listed as transcribable, so changing your mind costs one click rather than the talk.
    """
    manager = _manager(request)
    session = manager.state().get("session") or {}
    session_id = session.get("session_id", "")
    try:
        stats = await manager.cancel()
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
            else "The audio backend is missing. Reinstall the dependencies with: uv sync"
        ),
    }


@router.post("/audio/test")
async def test_device(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Open a capture device briefly and report whether it is actually producing usable audio.

    Enumerating a device proves only that the host knows about it. Whether the microphone is
    plugged into the socket the driver means, unmuted, and at a workable gain is a different
    question, and every one of those failures looks identical until the talk has started and the
    transcript is empty. No audio is retained (BE §17).
    """
    manager = _manager(request)
    if manager.is_running:
        raise HTTPException(
            status_code=409,
            detail=_error(
                "session-running",
                "Stop the recording before testing a device — they cannot both hold it.",
            ),
        )

    config = request.app.state.config.resolve()
    payload = body or {}
    # Falls back to the configured device, so the button works before anything is chosen.
    device_id = payload.get("device_id", config.audio.device_id)

    result = await run_in_threadpool(
        probe_device,
        device_id=device_id,
        seconds=float(payload.get("seconds", probe.DEFAULT_PROBE_SECONDS)),
        frame_ms=config.audio.frame_ms,
    )
    return result.as_dict()


@router.get("/audio/files")
async def get_audio_files(request: Request) -> dict[str, Any]:
    """Recordings the file source can replay.

    ``audio.file_path`` is a path on this machine, and a browser can neither browse that filesystem
    nor resolve one from a file picker. Without this list the only way to choose a recording is to
    edit the config file by hand — which is the kind of step this application exists not to need.
    """
    config = request.app.state.config.resolve()
    files = library.list_files(config.audio.file_path)
    return {
        "files": [file.as_dict() for file in files],
        "directory": str(library.library_dir()),
        "current": config.audio.file_path or "",
        "max_upload_bytes": library.MAX_UPLOAD_BYTES,
    }


@router.post("/audio/files")
async def upload_audio_file(request: Request, file: UploadFile = UPLOADED_FILE) -> dict[str, Any]:
    """Add a recording to the library and select it.

    Selecting it immediately is the point: uploading a file and then having to pick it from a list
    is two steps to express one intention.
    """
    try:
        stored = library.store_upload(file.filename or "recording.wav", await file.read())
    except library.AudioLibraryError as exc:
        raise HTTPException(status_code=422, detail=_error("upload-rejected", str(exc))) from exc

    request.app.state.config.update({"audio.source_type": "file", "audio.file_path": stored.path})
    return {"file": stored.as_dict(), "selected": True}


@router.delete("/audio/files")
async def delete_audio_file(request: Request, path: str) -> dict[str, Any]:
    """Remove a recording from the library. Refuses anything outside it."""
    try:
        removed = library.delete_file(path)
    except library.AudioLibraryError as exc:
        raise HTTPException(status_code=422, detail=_error("delete-refused", str(exc))) from exc

    config = request.app.state.config
    if removed and config.resolve().audio.file_path == path:
        # Leaving the pipeline pointed at a file that no longer exists turns a tidy-up into a
        # failure the next time the user presses record.
        config.update({"audio.file_path": None})
    return {"removed": removed}


@router.post("/audio/device")
async def select_device(request: Request, body: SelectDeviceRequest) -> dict[str, Any]:
    """Choose the capture source. Takes effect when the next session starts, and survives a restart.

    **This is the endpoint the Settings → Audio dropdown calls**, and it used to write to the
    runtime layer like every other setting — so choosing a microphone worked until the application
    was restarted, at which point it reverted with no explanation. That is the reported fault, and
    fixing `PATCH /api/config` alone did not fix it, because the dropdown does not go through that
    route. All three paths written here are in `PERSISTENT_PATHS`; see D-046 for why a device is
    not like a threshold.
    """
    config = request.app.state.config
    changes: dict[str, Any] = {
        "audio.source_type": body.source_type,
        "audio.device_id": body.device_id,
    }
    if body.file_path is not None:
        changes["audio.file_path"] = body.file_path

    hot_swap = config.persist(changes)
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
