"""One directory per recording, named for the moment it started.

Everything a single recording produces — the audio, the video, the preview frame, the measurement
sidecar — used to be written straight into ``data/recordings/`` with the session's stamp glued onto
the front of each name. After a fortnight of use that directory held several hundred files from
several dozen recordings, interleaved by name, and answering "what came out of the recording I made
on Tuesday" meant reading filenames.

So the recording, not the file, is the unit. Each one owns a directory named
``<YYYYMMDD-HHMMSS>-<session-id>``, and the files inside it have plain, fixed names.

**The directory name is deliberately identical to the transcript database's stem.** The transcript
lives in ``data/sessions/<same-name>.db`` and is *not* moved here: it is an open SQLite file for the
whole of a session, and relocating one mid-write to tidy a listing is a bad trade. Sharing the name
joins them without moving anything, which is what lets the past-sessions page say whether a session
has a video and lets the export find it.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

#: The audio the speech model reads. Always this name, whatever the session was called.
AUDIO_NAME = "audio.wav"
#: Measurements written beside the audio once a pass has run (`characterise.py`).
SIDECAR_NAME = "audio.json"
#: The video, minus its extension — the container depends on what GStreamer can encode.
VIDEO_STEM = "video"
#: The video with the session's audio muxed in. Preferred by the export; the sources are kept.
MUXED_STEM = "video-with-audio"
PREVIEW_NAME = "preview.jpg"

#: ``20260829-174113-d60b37a9e3c4`` — the stamp, then the session id. Anchored, because this is used
#: to decide whether a *directory* is one of ours and whether a stray *file* belongs in one.
KEY_PATTERN = re.compile(r"^(\d{8}-\d{6})-([0-9a-f]{6,32})$")
#: The same key, as the prefix of a flat file written before this module existed.
FLAT_PATTERN = re.compile(r"^(\d{8}-\d{6}-[0-9a-f]{6,32})(?:-(.+?))?(\.[^.]+)$")

#: Video containers this layout recognises, so a directory can be described without opening one.
VIDEO_SUFFIXES = (".webm", ".mkv", ".mp4", ".ogg", ".avi")


def _suffix(extension: str) -> str:
    return extension if extension.startswith(".") else f".{extension}"


def key_for(started_at: datetime, session_id: str) -> str:
    """The directory name for a recording that started at ``started_at``."""
    return f"{started_at.strftime('%Y%m%d-%H%M%S')}-{session_id}"


def is_key(name: str) -> bool:
    """Whether ``name`` is a recording key rather than some other thing in the directory."""
    return bool(KEY_PATTERN.match(name))


@dataclass(frozen=True)
class RecordingLayout:
    """Where one recording's files go, and what is actually there.

    Constructed from a key rather than from a path so a caller can never hand it a directory
    outside the recordings root — the join happens here, once, and `resolve` refuses an escape.
    """

    root: Path
    key: str

    @property
    def directory(self) -> Path:
        return self.root / self.key

    @property
    def audio(self) -> Path:
        return self.directory / AUDIO_NAME

    @property
    def sidecar(self) -> Path:
        return self.directory / SIDECAR_NAME

    @property
    def preview(self) -> Path:
        return self.directory / PREVIEW_NAME

    def video(self, extension: str) -> Path:
        """The video path for a container extension, with or without its leading dot."""
        return self.directory / f"{VIDEO_STEM}{_suffix(extension)}"

    def video_segment(self, index: int, extension: str) -> Path:
        """The ``index``-th piece of a capture that had to be restarted mid-session (D-036).

        Segment 1 is the plain ``video.<ext>``, so a recording that never broke is named exactly as
        it always was and nothing downstream has to know this exists. Later segments are
        ``video.002.<ext>`` and so on, because **appending to a finalised container is not a thing
        that can be done safely** — a piece that plays is worth more than one file that might not.
        They are joined at the end of the session by `capture/stitch.py`.
        """
        if index <= 1:
            return self.video(extension)
        return self.directory / f"{VIDEO_STEM}.{index:03d}{_suffix(extension)}"

    def video_segments(self) -> list[Path]:
        """Every segment on disk, in capture order. Empty when there is no video at all."""
        found: list[tuple[int, Path]] = []
        for suffix in VIDEO_SUFFIXES:
            first = self.directory / f"{VIDEO_STEM}{suffix}"
            if first.is_file() and first.stat().st_size > 0:
                found.append((1, first))
            for path in sorted(self.directory.glob(f"{VIDEO_STEM}.[0-9][0-9][0-9]{suffix}")):
                if path.stat().st_size == 0:
                    continue
                try:
                    found.append((int(path.name.split(".")[1]), path))
                except (IndexError, ValueError):
                    continue
        return [path for _index, path in sorted(found)]

    def ensure(self) -> Path:
        """Create the directory and return it."""
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory

    # -- what is in there ------------------------------------------------------------

    def existing_video(self) -> Path | None:
        """The best video to hand to a viewer: the muxed one when it exists, else the raw one."""
        return self.muxed_video() or self._video_named(VIDEO_STEM)

    def muxed_video(self) -> Path | None:
        """The video with the session's audio in it, if the mux ran and succeeded.

        Separate from :meth:`existing_video` because it answers a different question. That one asks
        "what should a viewer be shown"; this one asks "is the sound in the video", which is what
        makes a recording whose WAV has been transcribed away still count as having audio.
        """
        return self._video_named(MUXED_STEM)

    def _video_named(self, stem: str) -> Path | None:
        for suffix in VIDEO_SUFFIXES:
            candidate = self.directory / f"{stem}{suffix}"
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        return None

    def has_audio(self) -> bool:
        """Whether the separate WAV is still here — the file a transcription pass reads.

        Not the same question as "does this recording have sound", which is
        :meth:`has_playable_audio`. A successful pass *deletes* this file, so it is false for most
        healthy recordings.
        """
        return self.audio.is_file() and self.audio.stat().st_size > 44

    def has_playable_audio(self) -> bool:
        """Whether this recording has sound a person can listen to.

        **The WAV is not the only place sound lives.** A successful transcription pass deletes it —
        retention is off by default — but for a window recording the audio has already been muxed
        into `video-with-audio`, which is the file anyone actually plays. Testing only for the WAV
        reported "no audio" precisely when everything had gone right, and dragged the web-app export
        down with it: the one recording that had video, sound and a transcript was told it had no
        audio and offered no export.
        """
        return self.has_audio() or self.muxed_video() is not None

    def exists(self) -> bool:
        return self.directory.is_dir()

    def size_bytes(self) -> int:
        """Everything in the directory, added up. Zero when the directory is absent."""
        total = 0
        for path in self.directory.glob("*"):
            try:
                total += path.stat().st_size
            except OSError:  # noqa: PERF203 - deleted between the glob and the stat
                continue
        return total


def layout_for(root: Path | str, started_at: datetime, session_id: str) -> RecordingLayout:
    """The layout for a session that started at ``started_at``."""
    return RecordingLayout(Path(root), key_for(started_at, session_id))


def resolve_recording(root: Path | str, key: str) -> RecordingLayout | None:
    """Resolve a key that came from a URL, refusing anything outside ``root``.

    A key is user input. ``../../etc`` joined onto a directory is still a path traversal however
    harmless the caller looks, and this server is reachable from any page in any other tab.
    """
    if not is_key(key):
        return None
    directory = Path(root).resolve()
    candidate = (directory / key).resolve()
    if not candidate.is_relative_to(directory):
        logger.warning("Refusing a recording key that escapes the recordings directory: %r", key)
        return None
    return RecordingLayout(directory, key)


def iter_recordings(root: Path | str) -> Iterator[RecordingLayout]:
    """Every recording directory under ``root``, newest first.

    Ordering is by the key, which is a timestamp — not by mtime, which changes when a transcription
    pass writes a sidecar into a directory that is otherwise a week old.
    """
    directory = Path(root)
    try:
        names = sorted((path.name for path in directory.iterdir() if path.is_dir()), reverse=True)
    except OSError:
        return
    for name in names:
        if is_key(name):
            yield RecordingLayout(directory, name)


def started_at(key: str) -> datetime | None:
    """The moment a recording started, read back out of its key."""
    match = KEY_PATTERN.match(key)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S")
    except ValueError:
        return None


# -- migration ---------------------------------------------------------------------------


def migrate_flat_recordings(root: Path | str) -> int:
    """Move pre-layout files into per-recording directories. Returns how many moved.

    Runs once at start-up and is a no-op afterwards, because a directory with nothing loose in it
    has nothing to match. Every flat name already begins with the same ``<stamp>-<id>`` key this
    layout uses, so the grouping is unambiguous and needs no database.

    **Nothing is deleted and nothing is overwritten.** A file whose name does not parse is left
    exactly where it is: a recordings directory is a user directory, and a migration that tidied
    away something it did not recognise would be indistinguishable from data loss.
    """
    directory = Path(root)
    if not directory.is_dir():
        return 0

    moved = 0
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        match = FLAT_PATTERN.match(path.name)
        if not match:
            continue
        key, role, suffix = match.group(1), match.group(2) or "", match.group(3)
        target = directory / key / _migrated_name(role, suffix)
        if target.exists():
            logger.warning("Not migrating %s: %s already exists", path.name, target)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        except OSError as exc:
            logger.warning("Could not move %s into its recording folder: %s", path.name, exc)
            continue
        moved += 1

    if moved:
        logger.info("Moved %d loose recording files into per-recording folders", moved)
    return moved


def _migrated_name(role: str, suffix: str) -> str:
    """The in-folder name for a flat file, from the role its old name carried.

    The old scheme wrote ``<key>.wav``, ``<key>.webm``, ``<key>-preview.jpg``,
    ``<key>-with-audio.webm`` and ``<key>.json``; anything else keeps its role as a stem so an
    unexpected artefact still lands in the right folder under a name that says what it was.
    """
    if not role:
        if suffix == ".wav":
            return AUDIO_NAME
        if suffix == ".json":
            return SIDECAR_NAME
        return f"{VIDEO_STEM}{suffix}"
    if role == "preview":
        return PREVIEW_NAME
    if role == "with-audio":
        return f"{MUXED_STEM}{suffix}"
    return f"{role}{suffix}"
