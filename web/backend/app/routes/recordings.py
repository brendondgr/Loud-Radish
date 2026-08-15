"""Recordings on disk, and re-running a transcription over one (D-021).

These endpoints exist because a transcription pass can fail, and when it does the audio is the only
remaining copy of what was said. Without a way to list that audio and run the pass again, the
recovery documented in the failure message — "you can run the transcription again" — would be a
sentence with nothing behind it.

They also cover the crash case. Job state is deliberately not persisted, so a server restart during
a pass loses the job and keeps the recording; on the next start it appears here as unfinished, with
one call to finish it.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..models.session import SessionMetadata
from ..services.recording import (
    BatchError,
    TranscriptionJob,
    TranscriptionRunner,
    read_wav,
)
from ..services.session import modes
from ..services.transcript import TranscriptStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


def _manager(request: Request):  # noqa: ANN201 - returns SessionManager
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=503, detail=_error("not-ready", "The pipeline is starting.")
        )
    return manager


def _error(code: str, message: str, severity: str = "warning") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "severity": severity}}


def _recording_dir(request: Request) -> Path:
    return Path(request.app.state.config.resolve().recording.recording_dir)


def _resolve(request: Request, name: str) -> Path:
    """Resolve a recording by file name, refusing anything outside the recordings directory.

    The name comes from a URL. Without this an id of ``../../etc/passwd`` would be a file read on a
    process running as the user, which is a real hole even on a loopback-bound server — a page in
    any other tab can reach localhost.
    """
    directory = _recording_dir(request).resolve()
    candidate = (directory / name).resolve()
    if not candidate.is_relative_to(directory) or candidate.suffix != ".wav":
        raise HTTPException(
            status_code=422, detail=_error("bad-recording", "That is not a recording.")
        )
    if not candidate.is_file():
        raise HTTPException(
            status_code=404, detail=_error("no-recording", f"No recording named {name}.")
        )
    return candidate


@router.get("")
async def list_recordings(request: Request) -> dict[str, Any]:
    """Every recording still on disk, newest first.

    A recording present here has *not* been transcribed, or was kept deliberately: the pass deletes
    its audio on success unless audio retention is on. So this list is mostly a list of things that
    went wrong, which is exactly what it is for.
    """
    directory = _recording_dir(request)
    retain = request.app.state.config.resolve().storage.retain_audio

    files: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.wav"), reverse=True):
        try:
            stat = path.stat()
        except OSError:  # noqa: PERF203 - deleted between the glob and the stat
            continue
        entry: dict[str, Any] = {
            "name": path.name,
            "bytes": stat.st_size,
            "modified": stat.st_mtime,
            "duration_s": None,
        }
        try:
            _, entry["duration_s"] = read_wav(path)
        except BatchError:
            # Unreadable is worth *listing*, so it can be deleted. Hiding it would leave a file
            # nothing in the interface can explain or remove.
            entry["unreadable"] = True
        files.append(entry)

    return {
        "recordings": files,
        "directory": str(directory),
        "retain_audio": retain,
        "note": (
            "Recordings are deleted once transcribed. These were kept because a pass failed, "
            "was interrupted, or because audio retention is on."
        ),
    }


@router.post("/{name}/transcribe")
async def transcribe_recording(request: Request, name: str) -> dict[str, Any]:
    """Run — or re-run — a transcription pass over a recording on disk.

    Writes into a **new** session rather than the one that produced the recording. The original
    session's database may hold a partial transcript from a pass that failed halfway, and appending
    a second attempt to it would interleave two runs into one unreadable record.
    """
    manager = _manager(request)
    path = _resolve(request, name)

    if manager.is_running:
        raise HTTPException(
            status_code=409,
            detail=_error(
                "session-running",
                "Stop the current recording first — transcription and capture cannot share "
                "the speech model.",
            ),
        )
    if manager.jobs.is_busy:
        raise HTTPException(
            status_code=409,
            detail=_error(
                "transcription-running",
                "A transcription is already running. Wait for it to finish and try again.",
            ),
        )
    if not manager.asr.is_ready:
        raise HTTPException(
            status_code=409,
            detail=_error(
                "no-model",
                "No speech model is loaded. Load one in Settings → Speech model first.",
                "critical",
            ),
        )

    try:
        _, duration = read_wav(path)
    except BatchError as exc:
        raise HTTPException(status_code=422, detail=_error("unreadable", str(exc))) from exc

    config = request.app.state.config.resolve()
    session = SessionMetadata(session_id=uuid.uuid4().hex[:12], mode=modes.RECORDED)
    session.config = config.model_dump(mode="json")

    directory = Path(config.storage.session_dir)
    store = TranscriptStore(
        directory / f"{session.started_at.strftime('%Y%m%d-%H%M%S')}-{session.session_id}.db",
        metadata=session,
    )

    runner = TranscriptionRunner(
        registry=manager.jobs,
        emit=manager.emit,
        transcribe=manager.asr.transcribe,
        window_s=config.recording.batch_window_s,
        overlap_s=config.recording.batch_overlap_s,
        max_segment_s=config.streaming.max_segment_s,
    )
    job = TranscriptionJob(
        session_id=session.session_id,
        source_path=str(path),
        total_seconds=duration,
    )
    # Retention is forced on for a re-run. The audio was already kept once because something went
    # wrong with it, and deleting it on a second attempt that may also fail is how a talk is lost.
    if not runner.start(job=job, store=store, retain_audio=True):
        raise HTTPException(
            status_code=409,
            detail=_error("transcription-running", "A transcription is already running."),
        )

    logger.info("Re-transcribing %s into session %s", path.name, session.session_id)
    return {"started": True, "session_id": session.session_id, "job": job.as_event()}


@router.delete("/{name}")
async def delete_recording(request: Request, name: str) -> dict[str, Any]:
    """Remove a recording. Refuses one a transcription pass is currently reading."""
    manager = _manager(request)
    path = _resolve(request, name)

    current = manager.jobs.current
    if current is not None and current.is_running and current.source_path == str(path):
        raise HTTPException(
            status_code=409,
            detail=_error(
                "recording-in-use",
                "That recording is being transcribed right now. Wait for the pass to finish.",
            ),
        )

    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(
            status_code=422, detail=_error("delete-failed", f"Could not delete {name}: {exc}")
        ) from exc
    return {"removed": True, "name": name}
