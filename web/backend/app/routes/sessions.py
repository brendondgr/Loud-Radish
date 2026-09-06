"""Past sessions — listing, reading, exporting, and deleting.

The live application only ever knows about the session currently open. Once one stops, its
transcript lives in a SQLite file and every route under ``/api/transcript`` stops answering for it,
because those read the *running* session's store. That is correct for the live view and useless for
a talk that finished ten minutes ago, which is what these routes are for.

A session is addressed by its **key** — the file's stem — never by a path. A path from a request is
a path traversal waiting to be written, and there is no reason for the caller to supply one. That
same key names the recording's folder, which is what lets the web-app export here reach the video.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from ..services.export import DEFAULT_PRESET, PRESETS, ExportError, SourceProfile, build_webapp
from ..services.export import by_id as by_preset_id
from ..services.export import estimate as estimate_export
from ..services.export import probe as probe_media
from ..services.recording import resolve_recording
from ..services.transcript import archive
from ..services.transcript import export as render_export
from ..services.transcript import export_chat as render_chat_export
from ..services.transcript.store import TranscriptStore

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _config(request: Request):  # noqa: ANN202 - returns AppConfig
    return request.app.state.config.resolve()


def _open(request: Request, key: str) -> TranscriptStore:
    """Open a past session read-only, or 404 with a message that says what happened."""
    path = archive.find(_config(request), key)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "no-such-session",
                    "message": (
                        "That session is not in the session folder any more. "
                        "It may have been moved, deleted, or the folder changed in settings."
                    ),
                    "severity": "warning",
                }
            },
        )
    return TranscriptStore(path)


@router.get("")
async def list_sessions(request: Request) -> dict[str, Any]:
    """Every session on disk, newest first.

    Includes files that cannot be read, each with its reason. A session the user can see and cannot
    open is more useful than one that has silently vanished from the list.
    """
    config = _config(request)
    sessions = archive.list_sessions(config)
    running = getattr(request.app.state, "session_manager", None)
    return {
        "sessions": [session.as_dict() for session in sessions],
        "directory": str(archive.session_dir(config)),
        # So the page can mark the row that is still being written rather than presenting it as
        # finished with a duration that is about to change.
        "running_key": _running_key(running),
    }


def _running_key(manager: Any) -> str:
    """The key of the session being recorded *right now*, or empty when nothing is.

    **Gated on `is_running`, not on the store existing.** `SessionManager.store` deliberately keeps
    returning the last finished session's store so the assistant can still be asked about it
    (D-031) — so reading it alone marked every completed recording as still recording, gave it the
    "recording now" badge, and disabled its Delete button. The store answers "what can be read";
    only `is_running` answers "what is being written".
    """
    if manager is None or not getattr(manager, "is_running", False):
        return ""
    store = getattr(manager, "store", None)
    path = getattr(store, "path", None)
    return path.stem if path is not None else ""


@router.get("/{key}")
async def read_session(
    request: Request, key: str, limit: int = Query(2000, ge=1, le=20_000)
) -> dict[str, Any]:
    """One past session's transcript, summaries, and glossary."""
    store = _open(request, key)
    try:
        metadata = store.metadata()
        return {
            "key": key,
            "session": metadata.as_dict() if metadata else None,
            "stats": store.stats().as_dict(),
            # One transcription pass, not every row: a session holding both a live and a
            # post-capture pass would otherwise render the same talk twice on the page (D-022).
            "segments": [segment.as_event() for segment in store.latest_segments()[:limit]],
            "summaries": [summary.as_event() for summary in store.summaries()],
            "glossary": [term.as_event() for term in store.glossary()],
            "chat": [message.as_dict() for message in store.chat_history()],
        }
    finally:
        store.close()


@router.get("/{key}/export")
async def export_session(
    request: Request,
    key: str,
    fmt: str = Query("markdown"),
    include_chat: bool = Query(False),
) -> Response:
    """Download a past session in any of the five formats.

    **The conversation is not in it unless it is asked for.** Markdown and JSON used to carry the
    questions the user put to the assistant during the recording, because they were the two formats
    that could. But a transcript export is mostly a thing being handed to somebody else, and one
    person's half of a conversation is not part of the record of the talk. It exports on its own
    from ``/{key}/chat``; `include_chat=true` puts it back in here for anyone who wants one file.
    """
    store = _open(request, key)
    try:
        metadata = store.metadata()
        body, mime, extension = render_export(
            fmt,
            segments=store.latest_segments(),
            metadata=metadata,
            summaries=store.summaries(),
            glossary=store.glossary(),
            chat=store.chat_history() if include_chat else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": {"code": "unknown-format", "message": str(exc)}}
        ) from exc
    finally:
        store.close()

    stem = metadata.started_at.strftime("%Y%m%d-%H%M") if metadata else key
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{stem}-transcript.{extension}"'},
    )


@router.get("/{key}/chat")
async def export_chat_only(request: Request, key: str, fmt: str = Query("markdown")) -> Response:
    """Download the conversation the user had with the assistant during this recording.

    Its own document, because it belongs to the person who asked rather than to the talk. Empty is
    a valid answer and renders as a sentence saying so, rather than a 404 — "you asked nothing
    during this recording" is information, and an error code is not.
    """
    store = _open(request, key)
    try:
        metadata = store.metadata()
        body, mime, extension = render_chat_export(fmt, store.chat_history(), metadata)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error": {"code": "unknown-format", "message": str(exc)}}
        ) from exc
    finally:
        store.close()

    stem = metadata.started_at.strftime("%Y%m%d-%H%M") if metadata else key
    return Response(
        content=body,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{stem}-chat.{extension}"'},
    )


def _recording(request: Request, key: str):  # noqa: ANN202 - returns RecordingLayout
    """The recording folder for a session, or 404 saying there is not one."""
    layout = resolve_recording(archive.recording_dir(_config(request)), key)
    if layout is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "no-recording",
                    "message": "There is no recording folder for that session.",
                    "severity": "warning",
                }
            },
        )
    return layout


@router.get("/{key}/export/options")
async def export_options(request: Request, key: str) -> dict[str, Any]:
    """What this recording is, and what each preset would turn it into (D-037).

    **The measurement is the point.** A preset list on its own says "720p"; this says what 720p
    means for *this* file — the reported seminar asked for a 720p ceiling at capture and recorded
    at 2560 x 1532, so a window offering choices against the configured number would be describing
    a recording that does not exist.

    Every size is a range and says so. A constant-quality encode depends on content the estimator
    has not watched, and a figure presented as exact would be wrong in the way that matters, since
    the whole question is whether a file is small enough to send.
    """
    layout = _recording(request, key)
    video = layout.existing_video()
    source = probe_media(video) if video is not None else SourceProfile()

    return {
        "key": key,
        "source": source.as_dict(),
        "presets": [
            {**plan.as_dict(source), "estimate": estimate_export(plan, source).as_dict()}
            for plan in PRESETS
        ],
        "default": DEFAULT_PRESET.id,
        "chat_messages": len(_chat_count(request, key)),
        "job": _running_job(request, key),
    }


@router.post("/{key}/export/start")
async def start_export(
    request: Request,
    key: str,
    preset: str = Query(DEFAULT_PRESET.id),
    include_chat: bool = Query(False),
) -> dict[str, Any]:
    """Begin an export, and return the job to watch it by.

    A request, not a download. An encode is minutes for an hour of talk, and the old synchronous
    route held a browser connection open for the whole of it — tolerable for a stream copy and not
    for this. Progress arrives over the WebSocket; `export/result` collects the file.
    """
    if by_preset_id(preset) is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "unknown-preset",
                    "message": (
                        f"There is no export preset called {preset!r}. "
                        f"Available: {', '.join(plan.id for plan in PRESETS)}."
                    ),
                }
            },
        )

    layout = _recording(request, key)
    video = layout.existing_video()
    if video is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "not-exportable",
                    "message": (
                        "This recording has no video, so there is nothing for the player to show. "
                        "The transcript can still be exported as Markdown, SRT, or JSON."
                    ),
                    "severity": "warning",
                }
            },
        )

    runner = request.app.state.export_runner
    source = probe_media(video)
    job, plan = runner.plan_job(key=key, preset_id=preset, source=source)

    store = _open(request, key)
    metadata = store.metadata()
    path = store.path
    store.close()

    started = runner.start(
        job=job,
        plan=plan,
        source=source,
        session_path=path,
        metadata=metadata,
        layout=layout,
        config=_config(request),
        include_chat=include_chat,
    )
    if not started:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "export-busy",
                    "message": (
                        "An export is already running. Exports use every core this machine has, "
                        "so they run one at a time."
                    ),
                    "severity": "warning",
                }
            },
        )
    return {"started": True, "job": job.as_event()}


@router.get("/{key}/export/status")
async def export_status(request: Request, key: str) -> dict[str, Any]:
    """The current or most recent export for this session.

    The reconciliation path, exactly as `GET /api/session` is for the live pipeline: the WebSocket
    is how progress arrives and this is how a page that has just loaded finds out where things got
    to without waiting for the next frame.
    """
    return {"key": key, "job": _running_job(request, key)}


@router.post("/{key}/export/cancel")
async def cancel_export(request: Request, key: str) -> dict[str, Any]:
    """Stop an export that is running. The partial output is removed rather than left playable."""
    job = request.app.state.export_jobs.current()
    if job is None or job.key != key:
        return {"cancelled": False}
    job.cancel()
    return {"cancelled": True, "job": job.as_event()}


@router.get("/{key}/export/result")
async def export_result(request: Request, key: str) -> Response:
    """Download what the export produced.

    Separate from the request that started it, so a finished export survives a reload — the archive
    is on disk in the recording's own folder, and this is the route that hands it over.
    """
    job = request.app.state.export_jobs.current()
    if job is None or job.key != key or not job.output_path:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "no-export",
                    "message": "There is no finished export for that session to download.",
                    "severity": "warning",
                }
            },
        )
    path = Path(job.output_path)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "export-gone",
                    "message": "The exported file is no longer on disk. Export it again.",
                    "severity": "warning",
                }
            },
        )
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{key}-webapp.zip",
    )


def _running_job(request: Request, key: str) -> dict[str, Any] | None:
    job = request.app.state.export_jobs.current()
    return job.as_event() if job is not None and job.key == key else None


def _chat_count(request: Request, key: str) -> list[Any]:
    store = _open(request, key)
    try:
        return list(store.chat_history())
    finally:
        store.close()


@router.get("/{key}/webapp")
async def export_webapp(request: Request, key: str, include_chat: bool = Query(False)) -> Response:
    """Download this recording as a self-contained HTML web application, in a ZIP.

    Refused rather than degraded when the recording has no video or no transcript. A "web
    application" with an empty player and nothing to read is not the thing that was asked for, and
    shipping one under the same name disappoints quietly instead of explaining — so the refusal
    names which piece is missing and what to do about it.

    Built on a worker thread. A talk is hundreds of megabytes, and copying that much into an archive
    on the event loop stalls every other request including the transcript the user is watching.

    **The archive carries the talk and not the conversation about it**, unless `include_chat` says
    otherwise. The exported page's whole purpose is that whoever receives it connects their own
    model and asks their own questions; opening it to find the panel already half-full of someone
    else's questions, about a talk they have not watched, is the opposite of that.
    """
    config = _config(request)
    layout = resolve_recording(archive.recording_dir(config), key)
    if layout is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "no-recording",
                    "message": "There is no recording folder for that session.",
                    "severity": "warning",
                }
            },
        )

    store = _open(request, key)
    try:
        body = await run_in_threadpool(
            build_webapp,
            key=key,
            store=store,
            metadata=store.metadata(),
            layout=layout,
            config=config,
            include_chat=include_chat,
        )
    except ExportError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "not-exportable",
                    "message": str(exc),
                    "severity": "warning",
                }
            },
        ) from exc
    finally:
        store.close()

    return Response(
        content=body,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{key}-webapp.zip"'},
    )


@router.delete("/{key}")
async def delete_session(request: Request, key: str) -> dict[str, Any]:
    """Delete a past session file.

    Refuses the session that is currently recording: deleting the file out from under an open
    connection loses the talk in progress, which is the one thing this application must not do.
    """
    manager = getattr(request.app.state, "session_manager", None)
    if key and key == _running_key(manager):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "session-running",
                    "message": "That session is still recording. Stop it before deleting it.",
                }
            },
        )

    path = archive.find(_config(request), key)
    if path is None:
        return {"deleted": False}
    path.unlink()
    return {"deleted": True}
