"""What changing a setting costs (BE §13.3).

Every setting falls into one of three classes. The frontend reads this classification to decide
whether to warn the user before applying a change — a VAD threshold applies silently, swapping the
ASR model produces a visible gap in transcription, and changing the session directory cannot happen
while a session is running at all.

Anything not listed is **live** by default. That is the safe direction: a setting wrongly treated as
live applies immediately and can be corrected, whereas a setting wrongly treated as restart-session
blocks the user mid-talk.
"""

from __future__ import annotations

from enum import StrEnum


class HotSwapClass(StrEnum):
    """The cost of changing a setting while a session is running."""

    LIVE = "live"
    RESTART_STAGE = "restart-stage"
    RESTART_SESSION = "restart-session"


#: Dotted paths that require flushing and reinitialising one pipeline stage.
RESTART_STAGE_PATHS: frozenset[str] = frozenset(
    {
        "audio.source_type",
        "audio.device_id",
        "audio.file_path",
        "audio.frame_ms",
        "vad.enabled",
        "vad.detector",
        "asr.backend",
        "asr.model",
        "asr.device",
        "asr.precision",
        "asr.language",
        "asr.beam_size",
    }
)

#: Dotted paths that cannot change without stopping the session entirely.
RESTART_SESSION_PATHS: frozenset[str] = frozenset(
    {
        "storage.session_dir",
        "storage.retain_audio",
        # Both are read when a recording *opens* its file. Changing either mid-recording would
        # either move the file underneath the sink or move the cap past a recording already
        # written against the old one (D-021).
        "recording.recording_dir",
        "recording.max_minutes",
        # Every one of these is baked into the GStreamer launch line when capture starts, and the
        # pipeline cannot be rebuilt without dropping the portal stream and asking again.
        "capture.cursor_mode",
        "capture.frame_rate",
        "capture.max_height",
        "capture.preview",
        # Read by the companion when it registers with the desktop's shortcut service; it re-reads
        # them on change, so this classification is about the *session*, which they never touch.
    }
)

#: Human-readable consequence per class, shown by the frontend before it applies a change.
CLASS_CONSEQUENCE: dict[HotSwapClass, str] = {
    HotSwapClass.LIVE: "Applies immediately.",
    HotSwapClass.RESTART_STAGE: "Briefly interrupts transcription while that stage restarts.",
    HotSwapClass.RESTART_SESSION: "Requires stopping the current session.",
}


def classify(path: str) -> HotSwapClass:
    """Classify a dotted configuration path.

    A path is matched exactly, or by its nearest listed prefix, so ``llm.local.endpoint`` and
    ``asr.model`` resolve without every leaf being enumerated.
    """
    if _matches(path, RESTART_SESSION_PATHS):
        return HotSwapClass.RESTART_SESSION
    if _matches(path, RESTART_STAGE_PATHS):
        return HotSwapClass.RESTART_STAGE
    return HotSwapClass.LIVE


def _matches(path: str, paths: frozenset[str]) -> bool:
    if path in paths:
        return True
    return any(path.startswith(f"{listed}.") for listed in paths)


def classify_many(paths: list[str]) -> HotSwapClass:
    """Return the most disruptive class across ``paths``.

    Used when a settings form writes several values at once: the user is warned about the worst
    consequence, not the first one encountered.
    """
    order = [HotSwapClass.LIVE, HotSwapClass.RESTART_STAGE, HotSwapClass.RESTART_SESSION]
    worst = HotSwapClass.LIVE
    for path in paths:
        candidate = classify(path)
        if order.index(candidate) > order.index(worst):
            worst = candidate
    return worst
