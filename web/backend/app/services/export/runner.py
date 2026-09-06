"""Run one export on a background thread, reporting every stage as it goes (D-037).

Four stages, and the middle one is why any of this exists:

1. **Measure** — `ffprobe` the recording, so the plan resolves against real dimensions.
2. **Encode** — the re-encode, or a stream copy for `original`. Minutes, for an hour of talk.
3. **Transcript** — build the two JSON documents the exported page reads.
4. **Package** — write the page, the documents, and the media into the ZIP.

**Not on the event loop, and not in the request.** The old export was one synchronous
`GET /{key}/webapp` that copied hundreds of megabytes on a worker thread while the browser held a
connection open — tolerable for a copy, impossible for an encode that takes minutes. So the request
starts a job and returns, the progress arrives over the WebSocket, and a separate request collects
the file. A page reloaded mid-export re-attaches to the running job rather than starting a second.

**The result is written into the recording's own folder.** An export that finished while the user
was on another page is still there when they come back, and a partial one from an export that was
interrupted is somewhere they can see it rather than in a temporary directory they cannot.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...config.schema import AppConfig
from ...models.session import SessionMetadata
from ..recording.layout import RecordingLayout
from ..transcript.store import TranscriptStore
from .encode import EncodeError, encode
from .estimate import estimate, package_seconds
from .job import ExportJob, ExportRegistry, ExportStage
from .presets import DEFAULT_PRESET, by_id
from .profile import EncodePlan, SourceProfile
from .webapp import ExportError, build_webapp

logger = logging.getLogger(__name__)

#: Where a finished export lands inside the recording's folder.
EXPORT_DIR_NAME = "exports"

#: Measuring is one `ffprobe` and building the transcript is a few SQLite reads. They are given a
#: token weight rather than zero so the bar moves the moment the job starts — a progress bar that
#: sits at nothing for the first second reads as one that has not started.
MEASURE_WEIGHT = 0.01
TRANSCRIPT_WEIGHT = 0.02


class ExportRunner:
    """Drives one :class:`ExportJob` from a measurement to a file."""

    def __init__(
        self,
        *,
        registry: ExportRegistry,
        emit: Callable[[str, dict[str, Any]], None],
        progress_interval_s: float = 1.0,
    ) -> None:
        self._registry = registry
        self._emit = emit
        self._interval = progress_interval_s
        self._last_emit = 0.0
        self._thread: threading.Thread | None = None

    # -- planning ----------------------------------------------------------------------

    def plan_job(
        self, *, key: str, preset_id: str, source: SourceProfile
    ) -> tuple[ExportJob, EncodePlan]:
        """Build the job and its stages, weighted by what each is predicted to cost."""
        plan = by_id(preset_id) or DEFAULT_PRESET
        predicted = estimate(plan, source)
        package_s = package_seconds(predicted.bytes or source.size_bytes)

        # Weights are seconds, normalised. The encode dominates for anything but a copy, which is
        # the whole reason the bar has to be weighted rather than counting stages: four equal
        # quarters would sit at 25 % through the only part that takes any time.
        stages = [
            ExportStage(id="measure", label="Measuring the recording", predicted_s=1.0),
            ExportStage(
                id="encode",
                label="Copying the video" if plan.copy else "Encoding the video",
                predicted_s=predicted.seconds,
            ),
            ExportStage(id="transcript", label="Building the transcript", predicted_s=1.0),
            ExportStage(id="package", label="Packaging the web application", predicted_s=package_s),
        ]
        total = sum(max(0.1, stage.predicted_s) for stage in stages)
        for stage in stages:
            stage.weight = max(0.1, stage.predicted_s) / total
        # The two instant stages keep a floor, so the bar moves at the very start and at the very
        # end rather than only through the middle.
        stages[0].weight = max(stages[0].weight, MEASURE_WEIGHT)
        stages[2].weight = max(stages[2].weight, TRANSCRIPT_WEIGHT)

        job = ExportJob(
            id=uuid.uuid4().hex[:12],
            key=key,
            preset_id=plan.id,
            stages=stages,
            estimated_bytes=predicted.bytes,
        )
        return job, plan

    # -- running -----------------------------------------------------------------------

    def start(
        self,
        *,
        job: ExportJob,
        plan: EncodePlan,
        source: SourceProfile,
        session_path: Path,
        metadata: SessionMetadata | None,
        layout: RecordingLayout,
        config: AppConfig,
        include_chat: bool,
    ) -> bool:
        """Claim the slot and run the job on its own thread. False when one is already going."""
        if not self._registry.claim(job):
            return False

        self._thread = threading.Thread(
            target=self._run,
            args=(job, plan, source, session_path, metadata, layout, config, include_chat),
            name="export-runner",
            daemon=True,
        )
        self._thread.start()
        # One frame before any work, so the window has something to draw without waiting for the
        # first tick. Same reason `TranscriptionRunner` does it.
        self._publish(job, force=True)
        return True

    def _run(
        self,
        job: ExportJob,
        plan: EncodePlan,
        source: SourceProfile,
        session_path: Path,
        metadata: SessionMetadata | None,
        layout: RecordingLayout,
        config: AppConfig,
        include_chat: bool,
    ) -> None:
        try:
            media = self._encode_stage(job, plan, source, layout)
            if media is None:
                return
            archive = self._package_stage(
                job, session_path, metadata, layout, config, include_chat, media
            )
            if archive is None:
                return
        except Exception as exc:  # noqa: BLE001 - a background thread must never die silently
            logger.exception("The export failed")
            job.fail(f"The export failed: {exc}")
            self._emit("export.failed", job.as_event())
            return

        job.finish(str(archive), archive.stat().st_size)
        logger.info(
            "Exported %s as %s (%s, %.1f MB against %.1f MB predicted)",
            job.key,
            archive.name,
            plan.id,
            job.output_bytes / 1e6,
            job.estimated_bytes / 1e6,
        )
        self._emit("export.done", job.as_event())

    def _encode_stage(
        self, job: ExportJob, plan: EncodePlan, source: SourceProfile, layout: RecordingLayout
    ) -> Path | None:
        job.begin("measure")
        job.complete("measure", f"{source.width}x{source.height} at {source.frame_rate:g} fps")
        self._publish(job, force=True)

        if plan.copy:
            job.skip("encode", "Copied as recorded")
            self._publish(job, force=True)
            return Path(source.path)

        directory = layout.directory / EXPORT_DIR_NAME
        output = directory / f"{job.key}-{plan.id}.{plan.extension(source)}"
        job.begin("encode", f"to {plan.resolved_height(source)}p")
        try:
            encode(
                plan,
                source,
                output,
                on_progress=lambda fraction: self._on_encode_progress(job, fraction),
                should_stop=lambda: job.cancelled,
            )
        except EncodeError as exc:
            if job.cancelled:
                self._emit("export.failed", job.as_event())
                return None
            job.fail(str(exc), stage_id="encode")
            self._emit("export.failed", job.as_event())
            return None

        job.complete("encode", f"{output.stat().st_size / 1e6:.0f} MB")
        self._publish(job, force=True)
        return output

    def _package_stage(
        self,
        job: ExportJob,
        session_path: Path,
        metadata: SessionMetadata | None,
        layout: RecordingLayout,
        config: AppConfig,
        include_chat: bool,
        media: Path,
    ) -> Path | None:
        job.begin("transcript")
        store = TranscriptStore(session_path)
        try:
            # `latest_segments`, not `stats()`. A session transcribed live and again afterwards
            # holds both passes and `stats()` counts the table — 1683 against the 800-odd that
            # actually go into the archive, which is the same double-count D-035 found in the
            # sessions listing. The number shown here has to be the number exported.
            job.complete("transcript", f"{len(store.latest_segments())} segments")
            self._publish(job, force=True)

            job.begin("package")
            body = build_webapp(
                key=job.key,
                store=store,
                metadata=metadata,
                layout=layout,
                config=config,
                include_chat=include_chat,
                media_override=media,
                on_progress=lambda fraction: self._on_package_progress(job, fraction),
            )
        except ExportError as exc:
            job.fail(str(exc), stage_id="package")
            self._emit("export.failed", job.as_event())
            return None
        finally:
            store.close()

        directory = layout.directory / EXPORT_DIR_NAME
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / f"{job.key}-webapp.zip"
        archive.write_bytes(body)
        job.complete("package", f"{len(body) / 1e6:.0f} MB")
        return archive

    # -- reporting ---------------------------------------------------------------------

    def _on_encode_progress(self, job: ExportJob, fraction: float) -> None:
        job.advance("encode", fraction)
        self._publish(job)

    def _on_package_progress(self, job: ExportJob, fraction: float) -> None:
        job.advance("package", fraction)
        self._publish(job)

    def _publish(self, job: ExportJob, *, force: bool = False) -> None:
        """Emit at most once a second, unless something happened that must not be coalesced away.

        The same rate limit `TranscriptionRunner` applies, and for the same reason: ffmpeg reports
        several times a second and the hub coalesces, so sending every one spends frames to be
        dropped. A stage boundary is forced through, because it is the one thing a client cannot
        infer from a later frame.
        """
        now = time.monotonic()
        if not force and now - self._last_emit < self._interval:
            return
        self._last_emit = now
        self._emit("export.progress", job.as_event())
