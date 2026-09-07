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


def capture_unavailable(detail: str) -> Failure:
    """Window capture was asked for on a machine that cannot do it (D-022).

    Names the specific missing piece, which the probe already worked out. "Window capture
    unavailable" on its own is the message that generates support requests.
    """
    return Failure(
        code="capture-unavailable",
        message=f"The window could not be captured. {detail}",
        severity=Severity.WARNING,
        transcription_continues=True,
    )


def capture_window_closed() -> Failure:
    """The captured window was closed while recording (D-022).

    Explicitly not an error. People close windows, and the audio — which is the part that cannot be
    recreated — is still recording.
    """
    return Failure(
        code="capture-window-closed",
        message=(
            "The window you were recording was closed, so the video ended there. "
            "Audio is still recording."
        ),
        severity=Severity.WARNING,
        transcription_continues=True,
    )


def voice_detector_fell_back(reason: str) -> Failure:
    """Silero was asked for and could not be built, so the energy detector is running.

    Reported rather than logged. The previous behaviour — a `logger.warning` and nothing else —
    meant the application showed "silero" in its status while running the energy detector, on the
    developer's own machine, for months.
    """
    return Failure(
        code="voice-detector-fallback",
        message=(
            "The Silero voice detector could not be loaded, so the simpler energy detector is "
            f"being used instead. {reason}"
        ),
        severity=Severity.WARNING,
        remedy={"vad.detector": "energy"},
        remedy_label="Use the energy detector and stop asking",
    )


def capture_failed(detail: str) -> Failure:
    """The video recorder died. The session continues on audio alone (D-022)."""
    return Failure(
        code="capture-failed",
        message=(
            f"Video recording stopped: {detail} "
            "Audio and the transcript are unaffected and still running."
        ),
        severity=Severity.WARNING,
        transcription_continues=True,
    )


def capture_stalled(quiet_s: float) -> Failure:
    """The video recorder is alive and has stopped writing anything (D-036).

    **The failure that had no name.** A capture whose source stops delivering buffers keeps its
    process, keeps its file handle, and keeps answering "still running" to the only question the
    supervisor used to ask — so fourteen minutes of a seminar went unrecorded without a log line.
    Saying it while it is happening is the whole point: the recording is still going, and there is
    still time to do something about the window it is pointed at.
    """
    return Failure(
        code="capture-stalled",
        message=(
            f"The video has recorded nothing for {quiet_s:.0f} seconds. The window may have been "
            "minimised, closed, or frozen. Audio and the transcript are unaffected."
        ),
        severity=Severity.WARNING,
        transcription_continues=True,
    )


def capture_resumed() -> Failure:
    """The video is being written again.

    Reported so a stall banner is answered rather than left standing.
    """
    return Failure(
        code="capture-resumed",
        message="The video is recording again.",
        severity=Severity.INFO,
        transcription_continues=True,
    )


def capture_pieces_joined(pieces: int, filled_s: float) -> Failure:
    """A capture that was restarted has been put back onto one timeline (D-036)."""
    return Failure(
        code="capture-pieces-joined",
        message=(
            f"The video recording restarted {pieces - 1} time(s) and has been joined into one "
            f"file, holding the last frame across {filled_s:.0f} seconds it could not capture."
        ),
        severity=Severity.INFO,
        transcription_continues=True,
    )


def capture_pieces_kept(pieces: int) -> Failure:
    """The pieces could not be joined, so they are kept as they are.

    **Said, because the alternative is a recording that looks half its length.** Everything
    downstream reads one video file; if only the first piece is found, the rest is on disk beside
    it and invisible. Naming the count is what makes it findable.
    """
    return Failure(
        code="capture-pieces-kept",
        message=(
            f"The video recording restarted, and its {pieces} pieces could not be joined. They are "
            "all kept in the recording's folder, numbered in order, and each one plays on its own."
        ),
        severity=Severity.WARNING,
        transcription_continues=True,
    )


def video_ended_early(shortfall_s: float, audio_name: str) -> Failure:
    """The combined file is shorter than the recording, because the picture stopped first.

    **Said rather than absorbed.** The user is already being told the video ended; what they are
    not told, and would otherwise discover only by playing the file to the end, is that the sound
    kept going and the combined file does not carry all of it. Naming the surviving WAV is the
    point of the message — it is the only complete copy, and this run keeps it regardless of
    Settings → Storage.
    """
    minutes = shortfall_s / 60.0
    length = f"{minutes:.0f} minutes" if minutes >= 1.0 else f"{shortfall_s:.0f} seconds"
    return Failure(
        code="video-ended-early",
        message=(
            f"The video stopped {length} before the recording did, so the combined file is that "
            f"much shorter. The whole of the sound is kept in {audio_name}."
        ),
        severity=Severity.WARNING,
        transcription_continues=True,
    )


def window_audio_stopped() -> Failure:
    """Everything the tap was carrying went away while the recording was still running.

    A warning rather than a failure: the video keeps recording, and the person may have closed one
    tab of several deliberately. But from here the transcript will be empty, and finding that out
    afterwards is finding it out too late — which is the complaint this whole path exists to answer.
    """
    return Failure(
        code="window-audio-stopped",
        message=(
            "The window stopped sending audio — whatever was playing has closed or ended, so "
            "nothing more will be transcribed. Start it playing again, or stop and record with "
            "your microphone."
        ),
        severity=Severity.WARNING,
    )


def window_audio_not_delivering() -> Failure:
    """The tap was built correctly and carries nothing, so the whole output is recorded instead.

    Measured on a real fault: the sink was created, the browser's ports were linked, every link
    read ``active``, both nodes read ``running``, every gain read 1.0 — and the tap's monitor
    delivered bit-exact zeros while the same audio played through the speakers at peak 1.04. A
    native client linked into the identical tap in the same second came back at 440 Hz, so the
    mechanism works and the browser's ``pipewire-pulse`` stream is what does not feed it.

    A warning rather than a failure, because the recording is fine — it is simply wider than was
    asked for. Saying so matters: the transcript will now contain anything else the machine plays,
    and discovering that afterwards is discovering it too late.
    """
    return Failure(
        code="window-audio-not-delivering",
        message=(
            "This window's audio could not be captured on its own, so the machine's whole output "
            "is being recorded instead. The transcript will include any other sound that plays. "
            "Mute anything you do not want in it, or stop and record with your microphone."
        ),
        severity=Severity.WARNING,
    )
