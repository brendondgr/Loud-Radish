"""Transcribing a past recording that has no transcript, once the user has approved it (D-076).

A recording can be brought back to the main screen only when it has a transcript, because the
assistant answers from the text. One that has only audio or video — a `recorded` session whose pass
never ran, a window recording whose transcript was lost, a folder with no database at all — is
transcribed first. **Nothing here runs on its own**: the recording window shows what
:func:`plan_pass` found, and only the user pressing Transcribe calls :func:`start_pass`.

What runs is exactly what a `recorded` session runs when it stops: one batch pass over a 16 kHz WAV
in the recording's folder, through :class:`~..recording.TranscriptionRunner`, into the database that
shares the folder's name. Nothing else — no summary, no polish — is started on the user's behalf.
Three sources, in order of preference:

* ``resume`` — an interrupted pass left a checkpoint; it continues where it stopped (D-045).
* ``wav`` — the recording's own ``audio.wav`` is still on disk.
* ``video`` — the sound is taken out of the video into ``audio.wav`` first (``extract.py``).
"""

from __future__ import annotations

import logging
import threading
import wave
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from ...config import AppConfig
from ...models.session import SessionMetadata
from ..recording import (
    JobState,
    RecordingLayout,
    TranscriptionJob,
    TranscriptionRunner,
    pass_options,
    resolve_recording,
)
from ..recording.extract import extract_audio
from ..recording.layout import started_at as started_from_key
from ..transcript import TranscriptStore, archive

logger = logging.getLogger(__name__)

#: Held while a video's sound is being taken out, which happens before the pass registers itself as
#: busy. Without it, two quick approvals would both pass the busy check and both extract.
_preparing = threading.Lock()


class PassRefused(Exception):
    """Why a pass cannot be started, with the HTTP status and code the route answers with."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PassPlan:
    """What approving would do, shown to the user before they approve it."""

    key: str
    title: str
    source: str
    seconds: float
    model: str
    model_ready: bool
    orphan: bool
    #: The reason it cannot run, as ``{code, message}``, or ``None`` when it can.
    refusal: dict[str, str] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "source": self.source,
            "seconds": round(self.seconds, 1),
            "model": self.model,
            "model_ready": self.model_ready,
            "orphan": self.orphan,
            "refusal": self.refusal,
        }


def plan_pass(config: AppConfig, manager: Any, key: str) -> PassPlan:
    """Describe the pass that approving would start, or why there is none.

    Raises:
        PassRefused: only for a key that names no recording at all (404). Every other reason is
            returned in ``refusal``, because the dialog shows it rather than failing to open.
    """
    recordings = archive.recording_dir(config)
    layout = resolve_recording(recordings, key)
    db = archive.find(config, key)
    if db is not None:
        row = archive.describe(db, recordings)
    elif layout is not None and layout.exists():
        row = archive.describe_orphan(key, recordings)
    else:
        raise PassRefused(404, "no-such-recording", "That recording is not on disk any more.")

    media = row.media
    source = media.transcribe_source
    refusal: tuple[str, str] | None = None
    if row.problem:
        refusal = ("unreadable", row.problem)
    elif media.transcript:
        refusal = ("already-transcribed", "This recording already has a transcript.")
    elif not source:
        refusal = ("nothing-to-transcribe", media.reason)
    elif manager.is_running:
        refusal = ("session-running", "Stop the current recording first.")
    elif manager.jobs.is_busy or _preparing.locked():
        refusal = ("transcription-running", "Wait for the running transcription to finish.")

    seconds = _seconds(source, layout, db) if source and not refusal else 0.0
    return PassPlan(
        key=key,
        title=row.title,
        source=source,
        seconds=seconds,
        model=config.asr.model,
        model_ready=bool(manager.asr.is_ready),
        orphan=row.orphan,
        refusal={"code": refusal[0], "message": refusal[1]} if refusal else None,
    )


def prepare_audio(config: AppConfig, plan: PassPlan) -> None:
    """Take a video's sound out into ``audio.wav``, when that is the plan's source. Blocking.

    Raises:
        PassRefused: when the video cannot be read, has no sound, or another preparation is
            already running.
    """
    if plan.source != "video":
        return
    layout = _layout(config, plan.key)
    video = layout.muxed_video() or layout.existing_video()
    if video is None:
        raise PassRefused(409, "nothing-to-transcribe", "The video is not on disk any more.")
    if not _preparing.acquire(blocking=False):
        raise PassRefused(
            409, "transcription-running", "Wait for the running transcription to finish."
        )
    try:
        result = extract_audio(video, layout.audio)
    finally:
        _preparing.release()
    if not result.ok:
        raise PassRefused(422, "no-sound", result.reason)


def start_pass(
    config: AppConfig, manager: Any, plan: PassPlan, *, open_when_done: bool
) -> TranscriptionJob:
    """Start the approved pass. The audio must already be prepared (:func:`prepare_audio`).

    ``open_when_done`` brings the recording to the main screen when the pass finishes with a
    transcript — the dialog's checkbox, so even that is the user's choice.
    """
    layout = _layout(config, plan.key)
    db = archive.find(config, plan.key) or (archive.session_dir(config) / f"{plan.key}.db")
    session_id = plan.key.rsplit("-", 1)[-1]
    store = _open_or_create(db, plan.key)

    runner_kwargs: dict[str, Any] = {}
    if plan.source == "resume":
        checkpoint = store.resumable_pass()
        if checkpoint is None or not Path(checkpoint["source_path"]).is_file():
            store.close()
            raise PassRefused(410, "audio-gone", "The audio this pass was reading is gone.")
        revision = int(checkpoint["revision"])
        job = TranscriptionJob(
            session_id=session_id,
            source_path=str(checkpoint["source_path"]),
            total_seconds=float(checkpoint["total_seconds"]),
            next_start_s=float(checkpoint["next_start_s"]),
            next_segment_id=int(checkpoint["last_segment_id"]),
            transcribed_seconds=float(checkpoint["next_start_s"]),
        )
        runner_kwargs["prompt"] = checkpoint.get("prompt") or None
    else:
        revision = 0
        # Measured from the WAV itself, not from the plan: for a video the plan measured the
        # container, and the extracted sound is what the pass will actually read.
        job = TranscriptionJob(
            session_id=session_id,
            source_path=str(layout.audio),
            total_seconds=_seconds("wav", layout, None) or plan.seconds,
        )

    def on_released() -> None:
        # On the runner's thread, after it has closed the store. Opens the recording only for a
        # pass that finished: a cancelled or failed one has a partial transcript at best, and
        # putting that on the main screen would read as the whole talk.
        if not open_when_done or job.state is not JobState.DONE or manager.is_running:
            return
        row = archive.describe(db)
        if row.problem or row.segments == 0:
            return
        try:
            manager.view_recording(
                db, {"key": plan.key, "title": row.title, "started_at": row.started_at}
            )
        except Exception:  # noqa: BLE001 - a finished pass must not fail on the convenience
            logger.warning("Could not open %s after transcribing it", db.name, exc_info=True)

    runner = TranscriptionRunner(
        registry=manager.jobs,
        emit=manager.emit,
        transcribe=manager.asr.transcribe,
        **pass_options(config),
        revision=revision,
        on_released=on_released,
    )
    if plan.source == "resume":
        # Kept whatever retention says, as `/transcribe/resume` does: this audio has already
        # survived one interruption.
        started = runner.resume(job=job, store=store, retain_audio=True, **runner_kwargs)
    else:
        # The ordinary rule a stopped `recorded` session follows. For extracted audio that is
        # always safe — the video still holds the sound.
        started = runner.start(job=job, store=store, retain_audio=config.storage.retain_audio)
    if not started:
        store.close()
        raise PassRefused(
            409, "transcription-running", "Wait for the running transcription to finish."
        )
    manager.attach_runner(runner)
    logger.info("Transcribing %s from its %s, on the user's approval", plan.key, plan.source)
    return job


# -- helpers ---------------------------------------------------------------------------------------


def _layout(config: AppConfig, key: str) -> RecordingLayout:
    layout = resolve_recording(archive.recording_dir(config), key)
    if layout is None:
        raise PassRefused(404, "no-such-recording", "That recording is not on disk any more.")
    return layout


def _open_or_create(db: Path, key: str) -> TranscriptStore:
    """The recording's database, created for a folder that never had one.

    Created under the folder's own key, so the two stay joined by name (D-032) and the row that was
    "not transcribed" becomes the same recording with a transcript, not a second row.
    """
    if db.is_file():
        return TranscriptStore(db)
    started = started_from_key(key)
    metadata = SessionMetadata(session_id=key.rsplit("-", 1)[-1], mode="recorded")
    if started is not None:
        metadata.started_at = started.replace(tzinfo=UTC)
    db.parent.mkdir(parents=True, exist_ok=True)
    return TranscriptStore(db, metadata=metadata)


def _seconds(source: str, layout: RecordingLayout | None, db: Path | None) -> float:
    """How long the audio a pass would read is. Zero when it cannot be told cheaply."""
    try:
        if source == "resume" and db is not None:
            with TranscriptStore(db) as store:
                checkpoint = store.resumable_pass() or {}
            return float(checkpoint.get("total_seconds") or 0.0)
        if layout is None:
            return 0.0
        if source == "wav":
            with wave.open(str(layout.audio), "rb") as handle:
                return handle.getnframes() / float(handle.getframerate() or 1)
        if source == "video":
            import av

            video = layout.muxed_video() or layout.existing_video()
            with av.open(str(video)) as container:
                return (container.duration or 0) / 1_000_000
    except Exception:  # noqa: BLE001 - a length is a courtesy in a dialog, not a requirement
        logger.debug("Could not measure the audio for %s", layout, exc_info=True)
    return 0.0
