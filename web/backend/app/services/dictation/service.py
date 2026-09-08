"""Press a key, speak, press it again, and the words arrive where you were typing (D-049).

**Not a fourth capture mode.** `live`, `recorded` and `window` are the modes (D-020), mirrored in
`modes.js` and guarded by a test that keeps the two in step; a dictation is not a fourth kind of
capture but a short recording with somewhere to deliver the result. It also stays out of
`SessionManager`, which is at 782 lines against a 800-line cap and would gain a whole second
lifecycle for something that needs none of its machinery — no transcript store, no polish worker,
no WebSocket fan-out, no session folder.

What it does share is **the warm speech model**. `AsrLifecycle` is expensive to load and the
session manager already owns one; a dictation that loaded its own would spend several seconds
before it heard anything, which is the one thing this feature cannot afford.

## The shape of it, and why

The pipeline is `record → transcribe → tidy → clipboard → paste`, and two decisions in it were
forced by measurement rather than taste.

**The text is pasted exactly once.** Tidying the transcript through the language model was measured
at 8-10 seconds warm on this machine. It is tempting to paste the raw text immediately and replace
it when the tidied version arrives — and that is wrong: the second paste lands wherever the caret
has moved to in the meantime, so the user ends up with both versions, in two places, one of them
mid-word. So the tidy is bounded by `cleanup_timeout_s` and falls back to the raw transcript, and
whichever text wins is pasted once.

**The delivery runs in the background and the caller returns immediately.** The keystroke that
stops a dictation must not hold an HTTP request open for twenty seconds; the tray and the desktop's
own notifications report progress instead.

**The recording is cut where the speaker paused, never on a clock (D-061).** The first version
went through the batch pass's thirty-second windows and came back with "..." where a window had
cut a phrase in half. Now `pipeline.py` finds the silences, ends every chunk inside one, hands each
chunk to the model whole, and tidies each on its own — so a dictation can run for half an hour and
lose nothing at a boundary, and reaching the length limit delivers what was said rather than
dropping it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...config.schema import AppConfig
from ...desktop import clipboard, keystroke
from ...desktop.notification import notify
from ..audio.formats import SAMPLE_RATE
from ..recording.sink import WavSink
from ..vad import build_detector
from ..vad.base import VoiceActivityDetector
from . import pipeline

logger = logging.getLogger(__name__)

#: The states a dictation passes through. Strings rather than an enum so they cross the HTTP
#: boundary and reach the tray unchanged.
IDLE = "idle"
RECORDING = "recording"
TRANSCRIBING = "transcribing"
TIDYING = "tidying"
DELIVERING = "delivering"
DONE = "done"
ERROR = "error"

#: Below this much **captured audio**, whatever arrived is a cough or a key pressed twice.
#: Transcribing it wastes a second, and pasting the model's guess at silence into a document is
#: worse than doing nothing.
#:
#: Measured against the recording's own duration rather than the wall clock between the two
#: keypresses. They are nearly the same in real use and completely different when the microphone is
#: a stub, but more to the point the wall clock is not the question being asked: how long the key
#: was held says nothing about whether any sound was captured while it was.
MIN_SECONDS = 0.4

#: How long to wait for a cold speech model before giving up on this dictation. Loading `base` on
#: this machine takes a few seconds; a minute means something is wrong that waiting will not fix.
MODEL_LOAD_TIMEOUT_S = 60.0


class DictationError(RuntimeError):
    """Something a user needs told, in words they can act on."""


@dataclass
class DictationState:
    """What the dictation is doing, for the API, the tray, and the tests."""

    state: str = IDLE
    seconds: float = 0.0
    text: str = ""
    raw_text: str = ""
    tidied: bool = False
    delivered: bool = False
    error: str = ""
    backend: str = ""
    started_at: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    #: How many pieces the recording was cut into, each ending in a pause (D-061).
    chunks: int = 0

    @property
    def recording(self) -> bool:
        return self.state == RECORDING

    @property
    def busy(self) -> bool:
        """Whether a dictation is anywhere in its pipeline."""
        return self.state in (RECORDING, TRANSCRIBING, TIDYING, DELIVERING)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "recording": self.recording,
            "busy": self.busy,
            "seconds": round(self.seconds, 2),
            "text": self.text,
            "tidied": self.tidied,
            "delivered": self.delivered,
            "error": self.error,
            "backend": self.backend,
            "started_at": self.started_at,
            "timings": {name: round(value, 2) for name, value in self.timings.items()},
            "chunks": self.chunks,
        }

    def summary(self) -> str:
        """One line for the control script and the notification."""
        if self.state == ERROR:
            return self.error or "dictation failed"
        if self.state == DONE and self.text:
            words = len(self.text.split())
            where = "pasted" if self.delivered else "copied"
            return f"{where} {words} word{'s' if words != 1 else ''}"
        if self.state == DONE:
            return "nothing was said"
        return self.state


class DictationService:
    """Owns at most one dictation at a time.

    One at a time, deliberately: two dictations would race for the microphone, for the speech model
    and for the clipboard, and the loser would paste the wrong words into whatever is focused.
    """

    def __init__(
        self,
        *,
        config_provider: Callable[[], AppConfig],
        asr_provider: Callable[[], Any],
        backend_factory: Callable[[], Any],
        source_factory: Callable[[AppConfig], Any] | None = None,
        detector_factory: Callable[[AppConfig], VoiceActivityDetector] | None = None,
        session_busy: Callable[[], bool] = lambda: False,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
        notifier: Callable[..., Any] = notify,
    ) -> None:
        self._config_provider = config_provider
        self._asr_provider = asr_provider
        self._backend_factory = backend_factory
        self._source_factory = source_factory or _default_source
        # The detector that finds the pauses a recording is cut at. The configured one by
        # default, so the same Silero model that gates the live path marks the silences here.
        self._detector_factory = detector_factory or (lambda config: build_detector(config.vad))
        self._session_busy = session_busy
        self._emit = emit or (lambda _name, _payload: None)
        self._notify = notifier

        self._lock = threading.RLock()
        self._state = DictationState()
        self._source: Any = None
        self._sink: WavSink | None = None
        self._path: Path | None = None
        self._started: float = 0.0
        self._notification_id = 0
        self._worker: threading.Thread | None = None
        self._warming: threading.Thread | None = None
        #: Whether the running recording has hit `max_seconds`. Reset on every start.
        self._capped = False

    # -- what it is doing ------------------------------------------------------------

    def state(self) -> DictationState:
        with self._lock:
            snapshot = DictationState(**vars(self._state))
            if snapshot.state == RECORDING:
                snapshot.seconds = time.monotonic() - self._started
            return snapshot

    # -- the two halves of a keypress ------------------------------------------------

    def toggle(self) -> DictationState:
        """Start a dictation, or finish the one running. What one key does."""
        with self._lock:
            running = self._state.state == RECORDING
        return self.finish() if running else self.start()

    def start(self) -> DictationState:
        config = self._config_provider()
        if not config.dictation.enabled:
            raise DictationError("Dictation is switched off in Settings.")

        with self._lock:
            if self._state.busy:
                raise DictationError(f"A dictation is already {self._state.state}.")
        if self._session_busy():
            raise DictationError(
                "A recording is running. Dictation shares the microphone, so stop it first."
            )

        asr = self._asr_provider()
        if asr is None:
            raise DictationError("There is no speech model on this server.")

        started_at = datetime.now(UTC)
        path = _dictation_path(config, started_at)
        path.parent.mkdir(parents=True, exist_ok=True)
        sink = WavSink(path, max_minutes=config.dictation.max_seconds / 60.0)
        source = self._source_factory(config)

        # **Recording before the microphone opens, not after.** The first frame can arrive inside
        # `source.start` itself, and the cap it may trip needs the recording to already exist.
        with self._lock:
            self._source, self._sink, self._path = source, sink, path
            self._started = time.monotonic()
            self._capped = False
            self._state = DictationState(
                state=RECORDING, started_at=started_at.isoformat(timespec="seconds")
            )

        def on_frame(frame: Any) -> None:
            # The sink answers whether the cap was reached. Nothing said after it is captured,
            # so the dictation ends there and delivers what was — rather than staying "recording"
            # over a file that stopped growing.
            if sink.write(frame):
                self._cap_reached(sink)

        try:
            source.start(on_frame, self._on_source_error)
        except Exception as exc:
            with self._lock:
                self._source = self._sink = self._path = None
                self._state = DictationState(state=IDLE)
            sink.close()
            path.unlink(missing_ok=True)
            raise DictationError(f"The microphone could not be opened: {exc}") from exc

        # **The model is loaded while the user is already talking, not before they may start.**
        # Refusing a dictation because nothing has opened the browser yet defeats the whole point
        # of a key that works without one, and the load takes about as long as a sentence — so it
        # happens during the recording, in the background, and is waited for at the end.
        if not getattr(asr, "is_ready", False):
            self._warm(asr)

        self._announce("Listening…", "Press the key again when you have finished.")
        self._publish()
        return self.state()

    def _warm(self, asr: Any) -> None:
        """Start loading the speech model on a background thread. Idempotent and quiet."""
        with self._lock:
            if self._warming is not None and self._warming.is_alive():
                return

            wanted = self._config_provider().asr

            def load() -> None:
                try:
                    # **The resolved config, not the one the lifecycle was built with.** The
                    # session manager is constructed by `create_app` and the config file is read in
                    # the lifespan that runs afterwards, so a bare `load()` uses the built-in
                    # default — which is the mock backend. Caught by a dictation that transcribed
                    # a silent room into a confident sentence in 0.0 seconds.
                    asyncio.run(asr.load(wanted))
                except Exception as exc:  # noqa: BLE001 - reported when the words are needed
                    logger.info("Could not load the speech model for dictation: %s", exc)

            self._warming = threading.Thread(target=load, name="dictation-warm", daemon=True)
            self._warming.start()

    def finish(self) -> DictationState:
        """Stop recording and hand the rest to a worker thread. Returns straight away."""
        with self._lock:
            if self._state.state != RECORDING:
                raise DictationError("Nothing is being dictated.")
            source, sink, path = self._source, self._sink, self._path
            elapsed = time.monotonic() - self._started
            self._source = self._sink = None
            self._state.state = TRANSCRIBING
            self._state.seconds = elapsed

        _quietly(source.stop)
        _quietly(sink.close)

        captured = sink.samples / float(sink.sample_rate or SAMPLE_RATE)
        with self._lock:
            self._state.seconds = captured
        self._publish()

        if captured < MIN_SECONDS:
            self._settle(DONE, text="", delivered=False, seconds=captured)
            _discard(path)
            return self.state()

        self._worker = threading.Thread(
            target=self._deliver, args=(path, captured), name="dictation", daemon=True
        )
        self._worker.start()
        return self.state()

    def cancel(self) -> DictationState:
        """Throw the recording away. Nothing is transcribed and nothing is pasted."""
        with self._lock:
            source, sink, path = self._source, self._sink, self._path
            self._source = self._sink = None
            self._state = DictationState(state=IDLE)
        _quietly(source.stop if source else None)
        _quietly(sink.close if sink else None)
        _discard(path)
        self._announce("Dictation cancelled", "")
        self._publish()
        return self.state()

    def _cap_reached(self, sink: WavSink) -> None:
        """The recording hit `max_seconds`. Finish it, once, off the microphone's thread.

        Off that thread because `finish` stops the source, and a capture stream cannot be stopped
        from inside its own callback. Once, because the sink reports the cap on every frame after
        it. And only for the sink that is *still* this dictation's — a late frame from a source
        that has been told to stop must not end the next dictation.
        """
        with self._lock:
            if self._sink is not sink or self._capped:
                return
            self._capped = True
            minutes = self._config_provider().dictation.max_seconds / 60.0

        def stop() -> None:
            self._announce(
                f"Reached the {minutes:g}-minute limit", "Delivering what was said so far."
            )
            try:
                self.finish()
            except DictationError as exc:
                logger.debug("The cap arrived after the dictation had ended: %s", exc)

        threading.Thread(target=stop, name="dictation-cap", daemon=True).start()

    # -- the pipeline ----------------------------------------------------------------

    def _deliver(self, path: Path, captured: float) -> None:
        """Transcribe, tidy, copy, paste. Runs on its own thread; never raises out of it."""
        config = self._config_provider()
        timings: dict[str, float] = {"recorded": captured}
        chunks = 0
        try:
            mark = time.monotonic()
            raw_chunks, chunks = self._transcribe(path, config)
            timings["transcribe"] = time.monotonic() - mark
            raw = " ".join(raw_chunks)

            if not raw:
                self._settle(
                    DONE, text="", delivered=False, seconds=captured, timings=timings, chunks=chunks
                )
                _discard(path, keep=config.dictation.keep_audio)
                return

            text = raw
            tidied = False
            if config.dictation.cleanup == "llm":
                self._set_state(TIDYING)
                self._announce("Tidying…", raw[:120])
                mark = time.monotonic()
                tidied_chunks, tidied = pipeline.tidy_chunks(
                    raw_chunks,
                    config,
                    self._backend_factory,
                    on_progress=self._progress("Tidying…"),
                )
                text = " ".join(tidied_chunks)
                timings["tidy"] = time.monotonic() - mark

            self._set_state(DELIVERING)
            delivered, backend = self._hand_over(text, config)

            # **The tidying up happens before `done`, not after it.** Settling first published a
            # state that said the dictation had finished while the recording was still on disk and
            # the sidecar not yet written — so anything acting on "done" raced the cleanup. Caught
            # by a test that looked for the discarded audio and found it about two runs in five.
            _write_sidecar(path, text, raw)
            _discard(path, keep=config.dictation.keep_audio)
            _prune(Path(config.dictation.directory), config.dictation.keep_transcripts)

            self._settle(
                DONE,
                text=text,
                raw_text=raw,
                seconds=captured,
                tidied=tidied,
                delivered=delivered,
                backend=backend,
                timings=timings,
                chunks=chunks,
            )
        except Exception as exc:  # noqa: BLE001 - a background thread has nowhere to raise
            logger.exception("Dictation failed")
            self._settle(ERROR, error=str(exc), timings=timings, chunks=chunks)
            _discard(path, keep=True)

    def _transcribe(self, path: Path, config: AppConfig) -> tuple[list[str], int]:
        """The clip through the warm model, a pause-bounded chunk at a time (D-061).

        Returns the text of each chunk that said anything, and how many chunks there were.
        """
        asr = self._asr_provider()
        warming = self._warming
        if warming is not None and warming.is_alive():
            self._announce("Loading the speech model…", "This happens once.")
            warming.join(timeout=MODEL_LOAD_TIMEOUT_S)
        if not getattr(asr, "is_ready", False):
            raise DictationError(
                "The speech model would not load, so what you said could not be transcribed. "
                "The recording has been kept."
            )
        chunks = pipeline.cut_at_pauses(path, config, self._detector_factory(config))
        texts = pipeline.transcribe_chunks(
            chunks, asr.transcribe, on_progress=self._progress("Transcribing…")
        )
        return texts, len(chunks)

    def _progress(self, verb: str) -> pipeline.ProgressFn:
        """A notification per chunk — but only once there is more than one to count."""

        def report(index: int, total: int) -> None:
            if total > 1:
                self._announce(f"{verb} {index + 1} of {total}", "")

        return report

    def _hand_over(self, text: str, config: AppConfig) -> tuple[bool, str]:
        """Clipboard first, then the keystroke — and only if the clipboard really took it."""
        copied = clipboard.copy(text)
        if not copied.ok:
            raise DictationError(f"The clipboard would not take the text: {copied.describe()}")

        if not config.dictation.paste:
            self._announce("Copied to the clipboard", text[:160])
            return False, copied.backend

        # **Verified before pressing paste.** Pasting over a clipboard that still holds the
        # previous thing puts someone else's text into the document and looks like it worked.
        landed = clipboard.read_back()
        if landed and landed.strip() != text.strip():
            self._announce("Copied, but not pasted", "The clipboard did not take the new text.")
            return False, copied.backend

        pressed = keystroke.paste(config.dictation.paste_chord)
        if not pressed.ok:
            self._announce(
                "Copied to the clipboard",
                f"Could not paste it for you: {pressed.describe()}. Press paste yourself.",
            )
            return False, copied.backend

        self._announce("Pasted", text[:160])
        return True, pressed.backend

    # -- housekeeping ----------------------------------------------------------------

    def _on_source_error(self, error: Exception) -> None:
        logger.warning("The dictation microphone reported %s", error)

    def _set_state(self, state: str) -> None:
        with self._lock:
            self._state.state = state
        self._publish()

    def _settle(self, state: str, **fields: Any) -> None:
        with self._lock:
            current = vars(self._state)
            current.update(state=state, **fields)
            self._state = DictationState(**current)
        self._publish()
        final = self.state()
        if state == ERROR:
            self._announce("Dictation failed", final.error)
        elif state == DONE and not final.text:
            self._announce("Nothing was said", "The recording was silent.")

    def _publish(self) -> None:
        self._emit("dictation.state", self.state().as_dict())

    def _announce(self, summary: str, body: str) -> None:
        """One notification per dictation, replaced as it progresses rather than stacked."""
        try:
            result = self._notify(summary, body, replaces=self._notification_id)
        except Exception:  # noqa: BLE001
            return
        if getattr(result, "ok", False) and str(result.detail).isdigit():
            self._notification_id = int(result.detail)


# -- module helpers ------------------------------------------------------------------


def _default_source(config: AppConfig):  # noqa: ANN202
    from ..audio.sources.device import DeviceSource

    return DeviceSource(device_id=config.audio.device_id, frame_ms=config.audio.frame_ms)


def _dictation_path(config: AppConfig, when: datetime) -> Path:
    stamp = when.strftime("%Y%m%d-%H%M%S")
    return Path(config.dictation.directory).expanduser() / f"{stamp}.wav"


def _quietly(action: Callable[[], Any] | None) -> None:
    if action is None:
        return
    try:
        action()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Ignoring %s while tearing a dictation down", exc)


def _discard(path: Path | None, *, keep: bool = False) -> None:
    if path is None or keep:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove %s: %s", path, exc)


def _write_sidecar(path: Path, text: str, raw: str) -> None:
    """The words, next to where the audio was. Recoverable when a paste lands somewhere wrong."""
    import json

    try:
        path.with_suffix(".json").write_text(
            json.dumps(
                {"text": text, "raw": raw, "at": datetime.now(UTC).isoformat(timespec="seconds")},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("Could not write the dictation sidecar: %s", exc)


def _prune(directory: Path, keep: int) -> None:
    """Keep the newest ``keep`` transcripts and nothing else.

    Dictation is the one part of this application that produces a file every time a key is pressed.
    679 empty session databases were deleted one plan ago; this is what stops that happening again
    from the other end.
    """
    try:
        sidecars = sorted(directory.glob("*.json"), key=lambda item: item.name, reverse=True)
    except OSError:
        return
    for stale in sidecars[keep:]:
        _discard(stale)
        _discard(stale.with_suffix(".wav"))


__all__ = ["DictationError", "DictationService", "DictationState"]
