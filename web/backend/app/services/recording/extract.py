"""Taking a video's sound out, so a recording with no transcript can be transcribed (D-076).

The batch pass reads one format — a 16 kHz mono PCM WAV, the one :class:`~.sink.WavSink`
writes — and a recording whose WAV is gone (a successful pass deletes it) or never existed (a
media-only folder) has its sound only inside a video. This writes that sound back out as
``audio.wav`` in the recording's own folder, where every other part of the pass — the checkpoint,
the resume, the retention rule — already expects to find it.

**Through PyAV, not the ``ffmpeg`` binary.** The mux in ``capture/mux.py`` shells out to ``ffmpeg``,
which is not installed everywhere this runs; PyAV ships its own libraries in its wheel and is
already here, because ``faster-whisper`` decodes audio with it. A transcription feature that worked
only where an optional binary happened to be installed would fail on exactly the machine the
recording was made on.

Nothing here raises: a video that cannot be read, or has no sound, is an answer the user is shown,
not an error.
"""

from __future__ import annotations

import logging
import wave
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The format the batch pass reads (`batch.read_wav`).
SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class ExtractResult:
    """What came of extracting a video's sound."""

    ok: bool
    seconds: float = 0.0
    reason: str = ""


def has_sound(video: Path | str) -> bool:
    """Whether ``video`` holds an audio stream. False when it cannot be opened at all."""
    try:
        import av

        with av.open(str(video)) as container:
            return len(container.streams.audio) > 0
    except Exception:  # noqa: BLE001 - an unreadable file has no sound we can use
        logger.debug("Could not probe %s for sound", video, exc_info=True)
        return False


def extract_audio(video: Path | str, wav: Path | str) -> ExtractResult:
    """Write ``video``'s first audio stream to ``wav`` as 16 kHz mono 16-bit PCM.

    Written to a sibling ``.part`` file and renamed into place, so a crash mid-extraction never
    leaves a truncated ``audio.wav`` that the next pass would read as the whole recording.
    """
    source, target = Path(video), Path(wav)
    partial = target.with_name(f"{target.name}.part")
    try:
        import av

        with av.open(str(source)) as container:
            if not container.streams.audio:
                return ExtractResult(False, reason="This video has no sound to transcribe.")
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
            samples = 0
            with wave.open(str(partial), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(SAMPLE_RATE)
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        data = converted.to_ndarray()
                        out.writeframes(data.tobytes())
                        samples += data.shape[-1]
                for converted in resampler.resample(None):
                    data = converted.to_ndarray()
                    out.writeframes(data.tobytes())
                    samples += data.shape[-1]
    except Exception as exc:  # noqa: BLE001 - reported to the user, never raised
        partial.unlink(missing_ok=True)
        logger.warning("Could not take the sound out of %s: %s", source.name, exc)
        return ExtractResult(False, reason=f"The sound could not be read from the video: {exc}")

    if samples == 0:
        partial.unlink(missing_ok=True)
        return ExtractResult(False, reason="This video's sound track is empty.")

    partial.replace(target)
    seconds = samples / SAMPLE_RATE
    logger.info("Took %.1f s of sound out of %s", seconds, source.name)
    return ExtractResult(True, seconds=seconds)
