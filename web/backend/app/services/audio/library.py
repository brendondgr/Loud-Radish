"""The audio file library — recordings the file source can replay (BE §4.3).

``audio.file_path`` is a path on the machine running the server. A browser cannot enumerate that
filesystem and cannot resolve a path from a file picker, so without this module the only way to
select a recording is to edit the config file by hand. That is precisely the kind of
configure-it-from-outside step the application is meant not to have.

So the server keeps a directory of its own — ``data/audio`` — lists what is in it, and accepts
uploads into it. A path typed in by hand still works and is still validated; the library is the
convenient case, not the only one.

**Uploads are constrained deliberately.** The filename is reduced to a safe stem, the extension must
be ``.wav``, and the result is written inside the library directory and nowhere else. This is a
loopback-only single-user application, but an upload endpoint that accepts an arbitrary destination
path is a directory traversal whether or not anyone is listening on the network.
"""

from __future__ import annotations

import logging
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ... import paths

logger = logging.getLogger(__name__)

#: Where uploaded and hand-placed recordings live.
LIBRARY_DIR: Path = paths.DATA_DIR / "audio"

#: The only container the file source reads. Everything else is rejected at the door rather than
#: accepted and then failing later, when the user has pressed record and the talk has started.
ALLOWED_SUFFIXES = (".wav",)

#: Ceiling on an upload. Roughly three hours of 16 kHz mono 16-bit audio, which is longer than any
#: single talk this application is built for.
MAX_UPLOAD_BYTES = 350 * 1024 * 1024

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class AudioLibraryError(RuntimeError):
    """Raised when a file cannot be added or read. The message names the remedy."""


@dataclass(frozen=True)
class AudioFile:
    """One selectable recording."""

    path: str
    name: str
    size_bytes: int
    #: ``0.0`` when the header could not be read — the file is still listed, with the reason.
    duration_seconds: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    #: Empty when the file is usable. Otherwise says what is wrong with it.
    problem: str = ""
    #: False for a hand-typed path outside the library directory.
    in_library: bool = True

    @property
    def usable(self) -> bool:
        """Whether the file source can actually replay this."""
        return not self.problem

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``GET /api/audio/files``."""
        return {
            "path": self.path,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "duration_seconds": round(self.duration_seconds, 2),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "problem": self.problem,
            "in_library": self.in_library,
            "usable": self.usable,
        }


def library_dir() -> Path:
    """Return the library directory, creating it if absent."""
    return paths.ensure_dir(LIBRARY_DIR)


def describe(path: Path, in_library: bool = True) -> AudioFile:
    """Describe one file, reading its WAV header for duration and rate.

    A file that cannot be read is described rather than omitted. Silently dropping it from the list
    looks identical to it never having been uploaded, which sends the user looking in the wrong
    place entirely.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return AudioFile(
            path=str(path),
            name=path.name,
            size_bytes=0,
            problem=f"Cannot be read ({type(exc).__name__}).",
            in_library=in_library,
        )

    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return AudioFile(
            path=str(path),
            name=path.name,
            size_bytes=size,
            problem="Not a WAV file. Convert it first, for example with ffmpeg.",
            in_library=in_library,
        )

    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate() or 0
            channels = handle.getnchannels()
    except (wave.Error, OSError, EOFError) as exc:
        return AudioFile(
            path=str(path),
            name=path.name,
            size_bytes=size,
            problem=f"The WAV header could not be read ({type(exc).__name__}).",
            in_library=in_library,
        )

    return AudioFile(
        path=str(path),
        name=path.name,
        size_bytes=size,
        duration_seconds=frames / rate if rate else 0.0,
        sample_rate=rate,
        channels=channels,
        in_library=in_library,
    )


def list_files(current_path: str | None = None) -> list[AudioFile]:
    """List the library, newest first, with ``current_path`` included even if it lives elsewhere.

    Including the configured file matters: if it was set by hand or moved, a list that omits it
    makes the interface look as though nothing is selected while the pipeline still points at it.
    """
    directory = library_dir()
    try:
        entries = sorted(
            (entry for entry in directory.iterdir() if entry.is_file()),
            key=lambda entry: entry.stat().st_mtime,
            reverse=True,
        )
    except OSError as exc:
        logger.warning("Could not read the audio library at %s: %s", directory, exc)
        entries = []

    files = [describe(entry) for entry in entries if entry.suffix.lower() in ALLOWED_SUFFIXES]

    if current_path:
        current = Path(current_path)
        if not any(Path(file.path) == current for file in files):
            files.insert(0, describe(current, in_library=False))

    return files


def safe_name(filename: str) -> str:
    """Reduce an uploaded filename to a safe one inside the library.

    Only the final component is considered, so ``../../etc/passwd`` becomes ``passwd``, and
    everything outside a conservative character set is collapsed to an underscore.
    """
    stem = Path(filename or "recording.wav").name
    cleaned = _UNSAFE.sub("_", stem).strip("._") or "recording"
    if not cleaned.lower().endswith(".wav"):
        cleaned = f"{Path(cleaned).stem}.wav"
    return cleaned


def unique_path(name: str) -> Path:
    """Return a path in the library that does not exist yet, suffixing ``-2``, ``-3``… as needed.

    Overwriting silently would destroy a recording the user may not have another copy of.
    """
    directory = library_dir()
    candidate = directory / name
    if not candidate.exists():
        return candidate

    stem, suffix = Path(name).stem, Path(name).suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise AudioLibraryError(f"Too many files named like {name!r} already exist in the library.")


def store_upload(filename: str, data: bytes) -> AudioFile:
    """Write an uploaded recording into the library and describe it.

    Raises:
        AudioLibraryError: when the upload is empty, too large, or not a readable WAV. The file is
            removed again in the last case — leaving an unusable file in the library would put it in
            the picker, where selecting it fails at the least convenient moment.
    """
    if not data:
        raise AudioLibraryError("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise AudioLibraryError(
            f"That file is {len(data) / 1024 / 1024:.0f} MB, over the {limit_mb} MB limit. "
            f"Trim the recording, or point the file source at it by path instead."
        )

    # Checked against the name as sent, before sanitising. `safe_name` forces a `.wav` suffix, so
    # checking afterwards would rename `talk.mp3` to `talk.wav` and then reject it for a malformed
    # header — a true statement about a file the user never uploaded under that name.
    suffix = Path(Path(filename or "").name).suffix.lower()
    if suffix and suffix not in ALLOWED_SUFFIXES:
        raise AudioLibraryError(
            f"{suffix} files cannot be uploaded — only WAV. Convert it first, e.g. with ffmpeg."
        )

    name = safe_name(filename)
    target = unique_path(name)
    try:
        target.write_bytes(data)
    except OSError as exc:
        raise AudioLibraryError(f"Could not write to the audio library ({exc}).") from exc

    described = describe(target)
    if not described.usable:
        target.unlink(missing_ok=True)
        raise AudioLibraryError(f"{name} is not a readable WAV file. {described.problem}")
    return described


def delete_file(path: str) -> bool:
    """Remove a file, but only one inside the library.

    A delete endpoint that accepts any path is a delete endpoint for the whole filesystem, so this
    refuses anything outside the library rather than trusting the caller.
    """
    directory = library_dir().resolve()
    try:
        target = Path(path).resolve()
        target.relative_to(directory)
    except (OSError, ValueError) as exc:
        raise AudioLibraryError(
            "That file is outside the audio library, so it will not be deleted from here."
        ) from exc

    if not target.is_file():
        return False
    target.unlink()
    return True
