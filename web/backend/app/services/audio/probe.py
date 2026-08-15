"""Opening a capture device briefly to find out whether it actually works (BE §4.3).

Enumerating a device proves the host knows about it. It does not prove the microphone is plugged
into the socket the driver thinks it is, that it is unmuted, that the gain is somewhere usable, or
that the entry labelled "default" is the thing sitting on the desk. Every one of those failures
looks identical until the talk has started and the transcript is empty.

So this opens the device for a couple of seconds and reports what arrived. Three outcomes matter and
they have completely different remedies:

* **silent** — the device opens and produces frames of nothing. Unmuted? Right input? Right device?
* **clipping** — far too loud. The transcript will be poor and no model setting will fix it.
* **quiet** — audible but low. Usually fixable with gain, and worth saying before the talk.

No audio is retained. The samples are measured and dropped, which is the same promise the pipeline
makes about the session itself (BE §17).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from .formats import SAMPLE_RATE
from .level import measure, to_dbfs
from .sources.device import DeviceSource, DeviceUnavailableError

logger = logging.getLogger(__name__)

#: How long to listen. Long enough to catch a syllable if someone says one, short enough that
#: nobody presses the button and wonders whether it has hung.
DEFAULT_PROBE_SECONDS = 2.5

#: Below this peak the device is producing nothing worth calling audio. −60 dBFS is well under a
#: quiet room's noise floor, so a real open microphone will always clear it.
SILENT_PEAK = 0.001

#: Below this peak the signal is present but too low to transcribe well.
QUIET_PEAK_DBFS = -40.0

#: Above this the signal is loud enough to be worth warning about before it clips.
HOT_PEAK_DBFS = -3.0

#: A loud signal whose zero-crossing rate is below this is not audio.
#:
#: Every real acoustic signal oscillates about zero — that is what sound *is*. A loud capture that
#: never changes sign is a device handing back something that is not a waveform: an uninitialised
#: buffer, a channel-count mismatch, a DC-biased input. Observed on this project's own development
#: machine, where the ALSA ``default`` device returned samples peaking *above* digital full scale
#: with no zero crossings at all, while reporting itself as a perfectly ordinary microphone.
#:
#: Without this check the level meter reads "loud", the probe says "close to clipping", and the
#: user is told to lower a gain that is not the problem — while the VAD, correctly, gates every
#: frame and the transcript stays empty with no explanation.
MIN_CREDIBLE_ZCR = 0.002

#: Samples above digital full scale. Real captured audio cannot exceed 1.0 in a normalised float
#: format; anything that does has not come through a working conversion.
IMPOSSIBLE_PEAK = 1.0001


@dataclass(frozen=True)
class ProbeResult:
    """What a device produced when it was opened."""

    #: ``ok``, ``quiet``, ``hot``, ``clipping``, ``silent``, ``invalid``, or ``failed``.
    result: str
    #: Names the remedy. Never generic failure text.
    message: str
    device: str = ""
    seconds: float = 0.0
    rms: float = 0.0
    peak: float = 0.0
    peak_dbfs: float = 0.0

    @property
    def usable(self) -> bool:
        """Whether a talk recorded through this device would be worth transcribing."""
        return self.result in ("ok", "quiet", "hot")

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload for ``POST /api/audio/test``."""
        return {
            "result": self.result,
            "message": self.message,
            "device": self.device,
            "seconds": round(self.seconds, 2),
            "rms": round(self.rms, 5),
            "peak": round(self.peak, 5),
            "peak_dbfs": round(self.peak_dbfs, 1),
            "usable": self.usable,
        }


def probe_device(
    device_id: str | None = None,
    seconds: float = DEFAULT_PROBE_SECONDS,
    frame_ms: int = 32,
    source_factory: Any | None = None,
) -> ProbeResult:
    """Open ``device_id``, listen for ``seconds``, and describe what arrived.

    Never raises for an ordinary failure — a device that will not open is a *result*, because the
    caller is a button in a settings dialog and the user needs the reason rather than a traceback.
    """
    frames: list[np.ndarray] = []
    errors: list[str] = []
    done = threading.Event()

    build = source_factory or DeviceSource
    source = build(device_id=device_id, frame_ms=frame_ms)

    def on_error(exc: BaseException | None) -> None:
        if exc is not None:
            errors.append(str(exc))
        done.set()

    try:
        source.start(frames.append, on_error)
    except DeviceUnavailableError as exc:
        return ProbeResult(result="failed", message=str(exc))
    except Exception as exc:  # noqa: BLE001 - host audio layers raise a wide variety of errors
        logger.warning("Device probe failed to open %s: %s", device_id, exc)
        return ProbeResult(
            result="failed",
            message=(
                f"That device could not be opened ({type(exc).__name__}). "
                f"It may be in use by another application, or unplugged since the list was built."
            ),
        )

    try:
        # Waits on the error signal rather than sleeping blindly, so a device that dies immediately
        # reports in milliseconds instead of after the full window.
        done.wait(seconds)
    finally:
        try:
            source.stop()
        except Exception:  # noqa: BLE001 - a failure to close must not mask what was measured
            logger.debug("Device probe could not close cleanly", exc_info=True)

    name = getattr(getattr(source, "info", None), "name", "") or (device_id or "system default")

    if errors:
        return ProbeResult(
            result="failed",
            message=f"{name} stopped during the test: {errors[0]}",
            device=name,
        )

    if not frames:
        return ProbeResult(
            result="failed",
            message=(
                f"{name} opened but delivered no audio at all. "
                f"That usually means another application holds the device exclusively."
            ),
            device=name,
        )

    audio = np.concatenate(frames)
    level = measure(audio)
    return _describe(
        name,
        audio.size / SAMPLE_RATE,
        level.rms,
        level.peak,
        level.clipping,
        zero_crossing_rate(audio),
    )


def zero_crossing_rate(audio: np.ndarray) -> float:
    """How often the signal changes sign, over the parts of it that are loud enough to count.

    Measured only where there is signal: a recording that is mostly silence has a meaningless
    overall rate, dominated by dither in the quiet stretches.
    """
    if audio.size < 2:
        return 0.0
    peak = float(np.abs(audio).max())
    if peak <= 0.0:
        return 0.0
    # Relative to this recording's own peak rather than an absolute floor. A fixed threshold
    # discards every sample of a genuinely quiet device, leaves nothing to measure, and reports a
    # rate of zero — which is the signature this function exists to detect. Calling a quiet
    # microphone broken is worse than the bug it was guarding against.
    loud = audio[np.abs(audio) > max(1e-5, peak * 0.1)]
    if loud.size < 2:
        return 0.0
    return float(np.mean(np.abs(np.diff(np.signbit(loud)))))


def _describe(
    name: str,
    seconds: float,
    rms: float,
    peak: float,
    clipping: bool,
    zcr: float = 1.0,
) -> ProbeResult:
    """Turn a measurement into a verdict and a remedy."""
    peak_dbfs = to_dbfs(peak)
    common = {
        "device": name,
        "seconds": seconds,
        "rms": rms,
        "peak": peak,
        "peak_dbfs": peak_dbfs,
    }

    # Checked before loudness, because this failure *presents* as loudness and the advice for it —
    # turn the gain down — is wrong and wastes the user's time. Restricted to signals that are
    # actually loud: for a quiet one "it is quiet" is both true and the more useful thing to say.
    if (peak_dbfs >= QUIET_PEAK_DBFS or peak > IMPOSSIBLE_PEAK) and (
        zcr < MIN_CREDIBLE_ZCR or peak > IMPOSSIBLE_PEAK
    ):
        return ProbeResult(
            result="invalid",
            message=(
                f"{name} is returning data that is not audio — a loud signal that never crosses "
                f"zero. This is usually a virtual or default device that does not map to a real "
                f"input. Choose a specific microphone from the list instead."
            ),
            **common,
        )

    if peak < SILENT_PEAK:
        return ProbeResult(
            result="silent",
            message=(
                f"{name} is open but completely silent. Check that it is not muted, that the "
                f"microphone is plugged into the input this device refers to, and that the system "
                f"has not routed capture elsewhere."
            ),
            **common,
        )

    if clipping:
        return ProbeResult(
            result="clipping",
            message=(
                f"{name} is clipping at {peak_dbfs:.0f} dBFS. The waveform is being cut off, which "
                f"no model setting can recover — lower the input gain in your system sound "
                f"settings, or move the microphone further from the speaker."
            ),
            **common,
        )

    if peak_dbfs < QUIET_PEAK_DBFS:
        return ProbeResult(
            result="quiet",
            message=(
                f"{name} works, but it is quiet — peaking at {peak_dbfs:.0f} dBFS. Speech this low "
                f"transcribes poorly. Raise the input gain, or turn on 'Even out the volume' below."
            ),
            **common,
        )

    if peak_dbfs > HOT_PEAK_DBFS:
        return ProbeResult(
            result="hot",
            message=(
                f"{name} works and is loud — peaking at {peak_dbfs:.0f} dBFS, close to clipping. "
                f"Lower the input gain a little for headroom."
            ),
            **common,
        )

    return ProbeResult(
        result="ok",
        message=f"{name} is working — peaking at {peak_dbfs:.0f} dBFS, a healthy level.",
        **common,
    )
