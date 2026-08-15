"""Failure handling and graceful degradation (BE §15).

The guiding principle, and the reason this module exists separately from the manager that uses it:

    **Transcription is the critical path.** An LLM failure must never stop transcription. A chat
    error is an inconvenience; a lost transcript is a ruined seminar.

Every response here names what happened *and what to do about it*. "Connection failed" leaves the
user stuck mid-talk; "No server responded at localhost:11434 — check that Ollama is running" does
not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..streaming.guards import Severity

#: Smaller model to fall back to, per model name. Ordered downward.
#: Each step down is a model that is genuinely faster *and* still worth using. ``large-v3-turbo``
#: sits between ``large-v3`` and ``small`` rather than ``medium`` doing so, because it is both
#: faster and more accurate than ``medium`` — falling back to ``medium`` would cost quality to buy
#: speed that turbo gives away.
SMALLER_MODEL: dict[str, str] = {
    "large-v3": "large-v3-turbo",
    "large-v2": "large-v3-turbo",
    "large": "large-v3-turbo",
    "large-v3-turbo": "small",
    "medium": "small",
    "small": "base",
    "base": "tiny",
}


@dataclass(frozen=True)
class Failure:
    """One failure, with the remedy attached."""

    code: str
    message: str
    severity: Severity = Severity.WARNING
    #: A configuration change that would fix it, as dotted paths. The frontend offers this as a
    #: one-click action rather than making the user find the setting.
    remedy: dict[str, Any] | None = None
    #: Label for that action.
    remedy_label: str = ""
    #: A settings tab that fixes it, when no single configuration change would. Some failures have
    #: no one-click answer — "the device was unplugged" needs the user to pick a different one —
    #: and a labelled button with nothing behind it is worse than no button.
    opens_settings: str = ""
    #: Whether transcription can continue. Only an audio or model failure stops it.
    transcription_continues: bool = True

    def as_event(self) -> dict[str, Any]:
        """The ``error`` event payload."""
        return {
            "code": self.code,
            "message": self.message,
            "severity": str(self.severity),
            "remedy": self.remedy,
            "remedy_label": self.remedy_label,
            "opens_settings": self.opens_settings,
            "transcription_continues": self.transcription_continues,
        }


def smaller_model(model: str) -> str | None:
    """The next model down, or ``None`` when already at the smallest."""
    return SMALLER_MODEL.get(model)


def device_lost(device_name: str) -> Failure:
    """The capture device went away — usually unplugged.

    The transcript so far is intact and must stay on screen. Clearing it would be both alarming and
    wrong: nothing that was committed has become untrue.
    """
    return Failure(
        code="device-lost",
        message=(
            f"{device_name} stopped responding — it may have been unplugged. "
            "The transcript so far is safe. Choose another device to continue."
        ),
        severity=Severity.CRITICAL,
        remedy_label="Choose a device",
        opens_settings="audio",
        transcription_continues=False,
    )


def model_load_failed(model: str, detail: str) -> Failure:
    """The model would not load. ``detail`` already names the likely cause."""
    fallback = smaller_model(model)
    return Failure(
        code="model-load-failed",
        message=detail,
        severity=Severity.CRITICAL,
        remedy={"asr.model": fallback} if fallback else None,
        remedy_label=f"Use {fallback} instead" if fallback else "Choose another model",
        opens_settings="" if fallback else "asr",
        transcription_continues=False,
    )


def out_of_memory(model: str, device: str) -> Failure:
    """Allocation failed on the compute device."""
    fallback = smaller_model(model)
    if fallback:
        remedy: dict[str, Any] | None = {"asr.model": fallback}
        label = f"Switch to {fallback}"
    else:
        remedy = {"asr.device": "cpu"}
        label = "Run on the CPU instead"

    return Failure(
        code="out-of-memory",
        message=(
            f"Ran out of memory loading {model} on the {device}. "
            "A smaller model, or running on the CPU, will fit."
        ),
        severity=Severity.CRITICAL,
        remedy=remedy,
        remedy_label=label,
        transcription_continues=False,
    )


def falling_behind(real_time_factor: float, model: str) -> Failure:
    """Real-time factor below 1. The system will not recover on its own."""
    fallback = smaller_model(model)
    return Failure(
        code="falling-behind",
        message=(
            f"Transcription is running at {real_time_factor:.1f}× realtime and will not catch up. "
            + (
                f"Switching to {fallback} recovers within a few seconds."
                if fallback
                else "Try a smaller model or a faster compute device."
            )
        ),
        severity=Severity.CRITICAL,
        remedy={"asr.model": fallback} if fallback else None,
        remedy_label=f"Switch to {fallback}" if fallback else "",
    )


def dropped_audio(events: int, model: str = "") -> Failure:
    """Audio was discarded because the consumer could not keep up. Words were lost.

    Reports the hole *and* what to do about it. The cause is the same as
    :func:`falling_behind` — inference slower than real time — so the remedy is too, and a warning
    that a transcript has gaps in it without saying how to stop it recurring leaves the user
    watching the rest of the talk go the same way.
    """
    fallback = smaller_model(model) if model else None
    return Failure(
        code="dropped-audio",
        message=(
            f"{events} stretches of audio were dropped because transcription could not keep up. "
            f"Those words are missing from the transcript. "
            + (
                f"Switching to {fallback} stops it happening again."
                if fallback
                else "Try a smaller model or a faster compute device."
            )
        ),
        severity=Severity.WARNING,
        remedy={"asr.model": fallback} if fallback else None,
        remedy_label=f"Switch to {fallback}" if fallback else "",
        opens_settings="" if fallback else "asr",
    )


def disk_full(detail: str) -> Failure:
    """The session file could not be written.

    The transcript stays in memory and the session continues. Stopping would guarantee losing what
    a freed-up disk might still save.
    """
    return Failure(
        code="disk-full",
        message=(
            f"Could not write the session to disk ({detail}). "
            "Recording continues in memory — free some space to resume saving."
        ),
        severity=Severity.CRITICAL,
    )


def llm_unavailable(detail: str) -> Failure:
    """The language model could not be reached.

    Explicitly non-fatal. Chat is a tool; the transcript is the document.
    """
    return Failure(
        code="llm-unavailable",
        message=detail,
        severity=Severity.WARNING,
        remedy_label="Open assistant settings",
        opens_settings="llm",
        transcription_continues=True,
    )


def recording_capped(minutes: float) -> Failure:
    """A recording hit its duration cap and stopped writing (D-021).

    Reported rather than left silent. A recording that stopped without saying so is
    indistinguishable from one that failed, and the difference matters: everything captured up to
    the cap is intact and will still be transcribed.
    """
    return Failure(
        code="recording-capped",
        message=(
            f"Recording reached its {minutes:.0f}-minute limit and stopped capturing audio. "
            "Everything recorded so far is intact and will be transcribed. "
            "Raise the limit in Settings → Storage if you need longer sessions."
        ),
        severity=Severity.WARNING,
        opens_settings="storage",
        transcription_continues=True,
    )


def transcription_failed(path: str, detail: str) -> Failure:
    """The post-capture transcription pass failed (D-021).

    Names the recording, because it is now the only copy of what was said and the pass can be
    re-run against it. A message that said only "transcription failed" would leave a user believing
    the talk was lost when it is sitting on disk.
    """
    return Failure(
        code="transcription-failed",
        message=(
            f"The recording could not be transcribed: {detail} "
            f"The audio is kept at {path} — you can run the transcription again from "
            "Settings → Storage."
        ),
        severity=Severity.CRITICAL,
        remedy_label="Open storage settings",
        opens_settings="storage",
    )
