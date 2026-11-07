"""What can be done to one past recording: bring it to the main screen, transcribe it, delete it.

Split from ``sessions.py``, which reads and exports past sessions and was already past 600 lines.
These are the routes that *change* something — which store the main screen reads (D-076), or what
is on disk.

**Bringing a recording back reuses the retained-store slot** (D-031, D-041): the finished session's
store that the assistant, the transcript routes and the session clock already read through
``SessionManager.store``. Opening a recording puts a chosen file there instead; none of those
readers changed. A recording is addressed by its key — the database's stem, which is also its
recording folder's name — never by a path.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..schemas.api import TranscribeRecordingRequest
from ..services.asr.contract import AsrLoadError
from ..services.recording import resolve_recording
from ..services.session.past_pass import PassRefused, plan_pass, prepare_audio, start_pass
from ..services.transcript import archive

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

logger = logging.getLogger(__name__)


def _error(code: str, message: str, severity: str = "warning") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "severity": severity}}


def _config(request: Request):  # noqa: ANN202 - returns AppConfig
    return request.app.state.config.resolve()


def _manager(request: Request):  # noqa: ANN202 - returns SessionManager
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503, detail=_error("not-ready", "The pipeline is starting.")
        )
    return manager


def _refuse_while_busy(manager: Any) -> None:
    """The two states in which the main screen's store must not change underneath someone.

    A running session owns the store the main screen reads; a running pass owns the speech model
    and, often, a store of its own. Both are temporary, so the message says what to wait for.
    """
    if manager.is_running:
        raise HTTPException(
            status_code=409,
            detail=_error("session-running", "Stop the current recording first."),
        )
    if manager.jobs.is_busy:
        raise HTTPException(
            status_code=409,
            detail=_error("transcription-running", "Wait for the running transcription to finish."),
        )


def _being_transcribed(manager: Any, key: str) -> bool:
    """Whether the running pass is reading or writing this recording."""
    job = manager.jobs.current
    if job is None or not job.is_running:
        return False
    source = Path(job.source_path) if job.source_path else None
    return key.endswith(f"-{job.session_id}") or (source is not None and source.parent.name == key)


# -- the main screen (D-076) ---------------------------------------------------------------------


@router.post("/{key}/open")
async def open_recording(request: Request, key: str) -> dict[str, Any]:
    """Show a past recording on the main screen, so the assistant can be asked about it.

    **Only one with a transcript.** The assistant answers from the transcript text, so a recording
    with only audio or video has nothing to answer from yet — it is transcribed first, and only
    once the user has approved that (``POST /{key}/transcribe``).
    """
    manager = _manager(request)
    _refuse_while_busy(manager)

    path = archive.find(_config(request), key)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=_error("no-such-session", "That recording has no transcript to open."),
        )
    session = archive.describe(path)
    if session.problem:
        raise HTTPException(status_code=422, detail=_error("unreadable", session.problem))
    if session.segments == 0:
        raise HTTPException(
            status_code=409,
            detail=_error(
                "needs-transcription",
                "This recording has no transcript yet. Transcribe it first, then bring it back.",
            ),
        )

    try:
        manager.view_recording(
            path, {"key": key, "title": session.title, "started_at": session.started_at}
        )
    except Exception as exc:
        logger.warning("Could not open %s for the main screen", path.name, exc_info=True)
        raise HTTPException(
            status_code=422,
            detail=_error("unreadable", f"That recording could not be opened: {exc}"),
        ) from exc
    return manager.state()


@router.post("/close")
async def close_recording(request: Request) -> dict[str, Any]:
    """Stop showing a chosen recording. The main screen is empty afterwards, not some other one."""
    manager = _manager(request)
    manager.close_viewing()
    return manager.state()


# -- transcribing, once the user approves (D-076) ------------------------------------------------


def _refused(refusal: PassRefused) -> HTTPException:
    return HTTPException(status_code=refusal.status, detail=_error(refusal.code, refusal.message))


#: The HTTP status each planning refusal is answered with when the user approves anyway.
_REFUSAL_STATUS = {"unreadable": 422}


@router.get("/{key}/transcribe/plan")
async def transcription_plan(request: Request, key: str) -> dict[str, Any]:
    """What approving would do: the source, its length, the model, or why it cannot run.

    Read by the approval dialog before anything is started, so the user agrees to a described
    piece of work rather than to a button. Starts nothing.
    """
    manager = _manager(request)
    try:
        plan = await run_in_threadpool(plan_pass, _config(request), manager, key)
    except PassRefused as refusal:
        raise _refused(refusal) from refusal
    return plan.as_dict()


@router.post("/{key}/transcribe")
async def transcribe_recording(
    request: Request, key: str, body: TranscribeRecordingRequest | None = None
) -> dict[str, Any]:
    """Transcribe a recording with no transcript. **Only ever called by the user approving it.**

    Loads the speech model if it is not loaded, takes the sound out of the video when that is the
    only copy, then starts the same batch pass a stopped `recorded` session runs. Progress arrives
    as the ordinary ``transcription.*`` events; with ``open_when_done`` the recording comes to the
    main screen when the pass finishes, announced as ``session.viewing``.
    """
    manager = _manager(request)
    config = _config(request)
    wanted = body or TranscribeRecordingRequest()
    try:
        plan = await run_in_threadpool(plan_pass, config, manager, key)
    except PassRefused as refusal:
        raise _refused(refusal) from refusal
    if plan.refusal:
        status = _REFUSAL_STATUS.get(plan.refusal["code"], 409)
        raise HTTPException(
            status_code=status, detail=_error(plan.refusal["code"], plan.refusal["message"])
        )

    if not manager.asr.is_ready:
        try:
            await manager.asr.load(config.asr)
        except AsrLoadError as exc:
            raise HTTPException(
                status_code=409, detail=_error("model-load-failed", str(exc), "critical")
            ) from exc

    try:
        await run_in_threadpool(prepare_audio, config, plan)
        job = start_pass(config, manager, plan, open_when_done=wanted.open_when_done)
    except PassRefused as refusal:
        raise _refused(refusal) from refusal
    return {"started": True, "source": plan.source, "job": job.as_event()}


# -- deleting -------------------------------------------------------------------------------------


@router.delete("/{key}")
async def delete_session(request: Request, key: str) -> dict[str, Any]:
    """Delete a past recording: its transcript database **and** its recording folder.

    **Both, since D-076.** This used to remove only the database and leave the video and audio
    behind — harmless while nothing listed a folder without a database, and wrong once Recordings
    does: every deleted recording would have come straight back as "not transcribed". The page
    names what goes before asking.

    Refuses the session that is recording, and a recording a pass or an export is reading: deleting
    a file out from under an open writer loses the talk in progress, which is the one thing this
    application must not do. If the recording is the one held open for the main screen, it is
    released first, so nothing keeps a handle to a file that no longer exists.
    """
    manager = getattr(request.app.state, "session_manager", None)
    config = _config(request)
    path = archive.find(config, key)

    if manager is not None and manager.is_running:
        store = manager.store
        if store is not None and path is not None and store.path.resolve() == path.resolve():
            raise HTTPException(
                status_code=409,
                detail=_error(
                    "session-running",
                    "That session is still recording. Stop it before deleting it.",
                ),
            )
    if manager is not None and _being_transcribed(manager, key):
        raise HTTPException(
            status_code=409,
            detail=_error(
                "recording-in-use",
                "That recording is being transcribed right now. Wait for the pass to finish.",
            ),
        )
    exports = getattr(request.app.state, "export_jobs", None)
    job = exports.current() if exports is not None else None
    if job is not None and job.key == key and exports.is_busy:
        raise HTTPException(
            status_code=409,
            detail=_error("export-running", "That recording is being exported right now."),
        )

    deleted = False
    if path is not None:
        if manager is not None:
            manager.release_if_holding(path)
        path.unlink(missing_ok=True)
        deleted = True

    layout = resolve_recording(archive.recording_dir(config), key)
    if layout is not None and layout.exists():
        try:
            shutil.rmtree(layout.directory)
            deleted = True
        except OSError as exc:
            raise HTTPException(
                status_code=422,
                detail=_error(
                    "delete-failed",
                    f"The transcript was deleted, but its recording folder could not be: {exc}",
                ),
            ) from exc
    return {"deleted": deleted}
