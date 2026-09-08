"""Capturing audio to disk, and transcribing it once it is whole (D-021).

* ``layout`` — one directory per recording, named for the moment it started
* ``sink``  — writes the canonical stream to a WAV file while a session records
* ``batch`` — transcribes a finished file in one pass, a pause-bounded chunk at a time
* ``chunks`` — cuts a finished file into pieces that each end in a pause (D-061)
* ``job``    — the state of one such pass, and the one-at-a-time rule
* ``runner`` — runs a pass on its own thread, outliving the session that produced the recording

This is what `recorded` mode is made of, and half of what `window` mode uses. It exists because the
live path's two-to-four second commit latency buys nothing when nobody is reading along: with the
audio already on disk, the right answer is to transcribe the whole thing with every word of context
available rather than to decide what is safe to show before the talk has finished.
"""

from .batch import BatchError, plan_recording, read_wav, transcribe_file
from .chunks import Chunk, plan_chunks
from .job import JobRegistry, JobState, TranscriptionJob
from .layout import (
    RecordingLayout,
    iter_recordings,
    key_for,
    layout_for,
    migrate_flat_recordings,
    resolve_recording,
)
from .runner import TranscriptionRunner, pass_options
from .sink import RecordingLimitReached, SinkError, WavSink

__all__ = [
    "BatchError",
    "Chunk",
    "JobRegistry",
    "JobState",
    "RecordingLayout",
    "RecordingLimitReached",
    "SinkError",
    "TranscriptionJob",
    "TranscriptionRunner",
    "pass_options",
    "WavSink",
    "iter_recordings",
    "key_for",
    "layout_for",
    "migrate_flat_recordings",
    "plan_chunks",
    "plan_recording",
    "read_wav",
    "resolve_recording",
    "transcribe_file",
]
