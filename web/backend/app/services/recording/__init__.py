"""Capturing audio to disk, and transcribing it once it is whole (D-021).

* ``sink``  — writes the canonical stream to a WAV file while a session records
* ``batch`` — transcribes a finished file in one pass
* ``job``   — the state of one such pass, and the one-at-a-time rule

This is what `recorded` mode is made of, and half of what `window` mode uses. It exists because the
live path's two-to-four second commit latency buys nothing when nobody is reading along: with the
audio already on disk, the right answer is to transcribe the whole thing with every word of context
available rather than to decide what is safe to show before the talk has finished.
"""

from .batch import BatchError, plan_windows, read_wav, transcribe_file
from .job import JobRegistry, JobState, TranscriptionJob
from .sink import RecordingLimitReached, SinkError, WavSink

__all__ = [
    "BatchError",
    "JobRegistry",
    "JobState",
    "RecordingLimitReached",
    "SinkError",
    "TranscriptionJob",
    "WavSink",
    "plan_windows",
    "read_wav",
    "transcribe_file",
]
