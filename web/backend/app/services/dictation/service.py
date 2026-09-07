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
        session_busy: Callable[[], bool] = lambda: False,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
        notifier: Callable[..., Any] = notify,
    ) -> None:
        self._config_provider = config_provider
        self._asr_provider = asr_provider
        self._backend_factory = backend_factory
        self._source_factory = source_factory or _default_source
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

        try:
            source.start(sink.write, self._on_source_error)
        except Exception as exc:
            sink.close()
            path.unlink(missing_ok=True)
            raise DictationError(f"The microphone could not be opened: {exc}") from exc

        with self._lock:
            self._source, self._sink, self._path = source, sink, path
            self._started = time.monotonic()
            self._state = DictationState(
                state=RECORDING, started_at=started_at.isoformat(timespec="seconds")
            )
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

    # -- the pipeline ----------------------------------------------------------------

    def _deliver(self, path: Path, captured: float) -> None:
        """Transcribe, tidy, copy, paste. Runs on its own thread; never raises out of it."""
        config = self._config_provider()
        timings: dict[str, float] = {"recorded": captured}
        try:
            mark = time.monotonic()
            raw = self._transcribe(path)
            timings["transcribe"] = time.monotonic() - mark

            if not raw:
                self._settle(DONE, text="", delivered=False, seconds=captured, timings=timings)
                _discard(path, keep=config.dictation.keep_audio)
                return

            text = raw
            tidied = False
            if config.dictation.cleanup == "llm":
                self._set_state(TIDYING)
                self._announce("Tidying…", raw[:120])
                mark = time.monotonic()
                text, tidied = self._tidy(raw, config)
                timings["tidy"] = time.monotonic() - mark

            self._set_state(DELIVERING)
            delivered, backend = self._hand_over(text, config)
            self._settle(
                DONE,
                text=text,
                raw_text=raw,
                seconds=captured,
                tidied=tidied,
                delivered=delivered,
                backend=backend,
                timings=timings,
            )
            _write_sidecar(path, text, raw)
            _discard(path, keep=config.dictation.keep_audio)
            _prune(Path(config.dictation.directory), config.dictation.keep_transcripts)
        except Exception as exc:  # noqa: BLE001 - a background thread has nowhere to raise
            logger.exception("Dictation failed")
            self._settle(ERROR, error=str(exc), timings=timings)
            _discard(path, keep=True)

    def _transcribe(self, path: Path) -> str:
        """The whole clip through the warm model, in one pass."""
        from ..recording.batch import transcribe_file

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
        segments = transcribe_file(path, transcribe=asr.transcribe)
        return " ".join(segment.text.strip() for segment in segments if segment.text).strip()

    def _tidy(self, raw: str, config: AppConfig) -> tuple[str, bool]:
        """Punctuation and capitalisation, bounded. Falls back to ``raw`` rather than waiting."""
        from ...services.llm.contract import GenerationOptions, system, user
        from . import prompts

        async def ask() -> str:
            backend = self._backend_factory()
            options = GenerationOptions(
                temperature=0.0,
                max_output_tokens=max(256, len(raw.split()) * 6),
                extra=dict(prompts.NO_REASONING_EXTRAS),
            )
            return await backend.complete([system(prompts.DICTATION_PROMPT), user(raw)], options)

        async def bounded() -> str:
            return await asyncio.wait_for(ask(), timeout=config.dictation.cleanup_timeout_s)

        try:
            cleaned = asyncio.run(bounded()).strip()
        except TimeoutError:
            logger.info(
                "The tidy pass overran %.0f s; pasting what was said instead",
                config.dictation.cleanup_timeout_s,
            )
            return raw, False
        except Exception as exc:  # noqa: BLE001 - no model, no server, a refusal
            logger.info("Could not tidy the dictation (%s); pasting what was said", exc)
            return raw, False

        if not cleaned:
            return raw, False
        # **A guard, not a formality.** A model that answers a punctuation request with a paragraph
        # of its own has not tidied anything, and pasting that into someone's document is the worst
        # outcome this feature has. Compare word counts, the same check the polish pass makes.
        spoken, written = len(raw.split()), len(cleaned.split())
        if written > spoken * 2 + 8 or written < spoken * 0.5:
            logger.info(
                "The tidy pass returned %d words for %d; keeping what was said", written, spoken
            )
            return raw, False
        return cleaned, True

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
