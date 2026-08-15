"""The server-to-client event vocabulary (BE §12.2).

Every frame is ``{"event": "<name>", "data": {...}}``.

**The one rule that matters**, restated here because it is where the bugs are:

* ``transcript.committed`` → the client **appends**. Never modifies an existing entry.
* ``transcript.hypothesis`` → the client **replaces** the tentative tail wholly. It is not a list
  entry.

Treating the hypothesis as the last element of the segment list duplicates text on screen. It is a
single mutable element that always sits at the end.
"""

from __future__ import annotations

from typing import Any, Final

# -- session --------------------------------------------------------------------------
SESSION_STARTED: Final = "session.started"
SESSION_STOPPED: Final = "session.stopped"

# -- transcript -----------------------------------------------------------------------
TRANSCRIPT_COMMITTED: Final = "transcript.committed"
TRANSCRIPT_HYPOTHESIS: Final = "transcript.hypothesis"
#: A finished minute, rewritten for reading (D-018). The client hides the raw segments the block
#: names in ``source_ids`` and shows the block in their place; the segments themselves are still
#: held, so a client that does not understand this event simply keeps showing raw text.
TRANSCRIPT_POLISHED: Final = "transcript.polished"

# -- recording and the post-capture pass (D-021) --------------------------------------
#: How much audio the current recording has captured. Coalescing: only the latest matters.
RECORDING_PROGRESS: Final = "recording.progress"
#: How far the post-capture transcription pass has got. Also coalescing, and for the same reason —
#: a figure from ten seconds ago is worse than useless on a progress bar.
TRANSCRIPTION_PROGRESS: Final = "transcription.progress"
#: The pass finished. Critical: dropping it leaves the interface showing a progress bar for a pass
#: that ended, with no way to learn otherwise short of a reload.
TRANSCRIPTION_DONE: Final = "transcription.done"
#: The pass failed, and the recording is still on disk. Critical for the same reason, and because
#: the message names the file the audio survives in.
TRANSCRIPTION_FAILED: Final = "transcription.failed"

#: The window capture started, stopped, or failed (D-022). Critical: a client that missed the
#: window-closed frame would keep showing a live preview of a capture that ended.
CAPTURE_STATE: Final = "capture.state"

# -- health ---------------------------------------------------------------------------
AUDIO_LEVEL: Final = "audio.level"
VAD_STATE: Final = "vad.state"
STATUS: Final = "status"
ASR_PROGRESS: Final = "asr.progress"

# -- context --------------------------------------------------------------------------
SUMMARY_ADDED: Final = "summary.added"
GLOSSARY_ADDED: Final = "glossary.added"

# -- chat -----------------------------------------------------------------------------
CHAT_DELTA: Final = "chat.delta"
CHAT_DONE: Final = "chat.done"

# -- problems -------------------------------------------------------------------------
ERROR: Final = "error"

#: Every event the server may send. The frontend's handler map is checked against this.
ALL_EVENTS: Final[tuple[str, ...]] = (
    SESSION_STARTED,
    SESSION_STOPPED,
    TRANSCRIPT_COMMITTED,
    TRANSCRIPT_HYPOTHESIS,
    TRANSCRIPT_POLISHED,
    RECORDING_PROGRESS,
    TRANSCRIPTION_PROGRESS,
    TRANSCRIPTION_DONE,
    TRANSCRIPTION_FAILED,
    CAPTURE_STATE,
    AUDIO_LEVEL,
    VAD_STATE,
    STATUS,
    ASR_PROGRESS,
    SUMMARY_ADDED,
    GLOSSARY_ADDED,
    CHAT_DELTA,
    CHAT_DONE,
    ERROR,
)

#: Events safe to drop when a client is slow: only the latest carries meaning. A level meter frame
#: from two seconds ago is worse than useless — it would draw a stale bar.
COALESCING_EVENTS: Final[frozenset[str]] = frozenset(
    {
        TRANSCRIPT_HYPOTHESIS,
        AUDIO_LEVEL,
        VAD_STATE,
        STATUS,
        RECORDING_PROGRESS,
        TRANSCRIPTION_PROGRESS,
    }
)

#: Events that must never be dropped, whatever the backlog. Losing one loses transcript.
CRITICAL_EVENTS: Final[frozenset[str]] = frozenset(
    {
        TRANSCRIPT_COMMITTED,
        # Dropping one would leave a minute of raw text on an otherwise polished page for the rest
        # of the session — the block is produced once and never re-sent.
        TRANSCRIPT_POLISHED,
        SESSION_STARTED,
        SESSION_STOPPED,
        # A transcription that ended and never said so leaves a progress bar running forever.
        TRANSCRIPTION_DONE,
        TRANSCRIPTION_FAILED,
        CAPTURE_STATE,
        CHAT_DELTA,
        CHAT_DONE,
        ERROR,
    }
)


def envelope(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload in the frame shape the client parses."""
    return {"event": event, "data": data}


def is_coalescing(event: str) -> bool:
    """Whether an older instance of this event may be discarded in favour of a newer one."""
    return event in COALESCING_EVENTS
