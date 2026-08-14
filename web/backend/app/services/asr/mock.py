"""A scripted backend that replays predetermined output (BE §20 M3).

The commit policy in Phase 5 is the genuinely hard part of this system, and it cannot be tested
deterministically against a real model — a real model's output varies with the audio, the hardware,
and the phase of the moon. This backend replays an exact word sequence, so every pathological case
the commit policy must survive can be written down and asserted.

It also models the behaviour that makes the commit policy necessary in the first place:
**a model revises its own output as more context arrives.** "matrix" in one pass becomes
"the matrix" in the next. :class:`MockAsrBackend` reproduces that with a configurable revision
depth, so LocalAgreement is tested against the thing it exists to handle rather than against a
model that happens to be stable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..audio.formats import SAMPLE_RATE
from .contract import AsrBackend, AsrCapabilities, AsrResult, WordToken

#: Position-encoded audio carries each sample's absolute session time in its value, so the mock can
#: answer the question a real model implicitly answers: *which words are in the audio I was
#: actually handed?* Without it the mock returns the start of its script no matter which slice of
#: the session it is given, which makes the engine's trimming and rebasing untestable — every trim
#: would look like the model repeating the opening of the talk.
POSITION_BASE = 0.5
#: Encoded time is ``POSITION_BASE + seconds / POSITION_SCALE``. float32 resolves roughly 6e-8 at
#: this magnitude, so this scale gives about 60 microseconds of timing precision — well under one
#: sample — over sessions up to a few hundred seconds, which is ample for tests.
POSITION_SCALE = 1_000.0


def positional_audio(
    start_seconds: float, duration_seconds: float, sample_rate: int = SAMPLE_RATE
) -> np.ndarray:
    """Build audio whose samples encode their own absolute session time.

    Values sit around :data:`POSITION_BASE`, well clear of the silence threshold, so this behaves
    like speech to every other part of the pipeline.
    """
    count = int(round(duration_seconds * sample_rate))
    times = start_seconds + np.arange(count, dtype=np.float64) / sample_rate
    return (POSITION_BASE + times / POSITION_SCALE).astype(np.float32)


def decode_position(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float | None:
    """Recover the absolute session time of sample zero, or ``None`` if unencoded.

    The encoding must be impossible to confuse with real audio, and a value in the right *range*
    is not enough — a loud recording reaches 0.7 and would decode as a plausible timestamp,
    silently sending the mock hunting for words hundreds of seconds into its script. So the check
    is on shape rather than magnitude: encoded audio is a **monotonically non-decreasing ramp**
    whose span matches its own duration. Speech is never non-decreasing across a whole buffer.
    """
    if audio.size < 2:
        return None

    first = float(audio[0])
    if not (POSITION_BASE - 1e-3) <= first <= (POSITION_BASE + 1.0):
        return None

    # A ramp, not a waveform. One sample out of order is enough to rule it out.
    if np.any(np.diff(audio) < -1e-7):
        return None

    expected_span = (audio.size - 1) / (sample_rate * POSITION_SCALE)
    actual_span = float(audio[-1]) - first
    if abs(actual_span - expected_span) > max(1e-6, expected_span * 0.05):
        return None

    return (first - POSITION_BASE) * POSITION_SCALE


#: What the mock says when nothing else is scripted. A backend whose purpose is "run the pipeline
#: without a real model" is useless if it produces nothing, so the default is a plausible stretch of
#: seminar speech rather than an empty list.
DEFAULT_SCRIPT_TEXT = (
    "the central claim is that these two operators commute only on the dense subspace "
    "where both are essentially self adjoint. outside it the bracket simply is not defined. "
    "that sounds like a technicality but it is not. almost every physical argument you have "
    "seen for the uncertainty relation quietly assumes the bracket exists everywhere. "
    "so let me set the counterexample up properly. take the position operator on the half "
    "line and take the generator of dilations alongside it. both are symmetric on smooth "
    "compactly supported functions. neither is self adjoint there and the deficiency indices "
    "differ which is the whole point of this example."
)


@dataclass
class MockScript:
    """What the mock will produce, and how it will misbehave.

    Attributes:
        words: the full transcript the mock is working towards, in order.
        words_per_second: how quickly words are "spoken", so timestamps look plausible.
        revise_last: how many trailing words the mock rewrites between passes. This is the
            behaviour LocalAgreement exists to absorb; ``0`` gives a perfectly stable model, which
            is exactly the model that hides bugs in the commit logic.
        revision_suffix: appended to revised words, so a revision is visible in a failure message.
        hallucinate_on_silence: emit text for silent input, reproducing the autoregressive failure
            mode the silence gate exists to prevent.
        unstable_tail: rewrite this many trailing words *differently on every pass*, so they can
            never agree. Models sometimes do this at a buffer edge, and it is the case the
            commit timeout exists to rescue.
        repeat_ngram: when set, degenerate into looping this phrase, reproducing Whisper's
            repetition failure mode so the repetition filter can be tested.
        inference_seconds: simulated inference cost, for real-time factor tests.
    """

    words: list[str] = field(default_factory=list)
    words_per_second: float = 2.5
    revise_last: int = 0
    revision_suffix: str = "'"
    unstable_tail: int = 0
    hallucinate_on_silence: bool = False
    repeat_ngram: list[str] | None = None
    repeat_after_pass: int = 0
    inference_seconds: float = 0.0
    confidence: float | None = None


class MockAsrBackend(AsrBackend):
    """Replays a :class:`MockScript`, scaled to the duration of the submitted audio."""

    def __init__(
        self,
        script: MockScript | None = None,
        capabilities: AsrCapabilities | None = None,
        model_id: str = "mock:scripted",
    ) -> None:
        self._script = script or MockScript()
        self._capabilities = capabilities or AsrCapabilities(
            word_timestamps=True,
            streaming_native=False,
            accepts_prompt=True,
            languages=["*"],
            max_audio_seconds=30.0,
            runs_on="cpu",
            confidence=script.confidence is not None if script else False,
        )
        self._model_id = model_id
        self._loaded = False
        self._pass_count = 0
        #: Fallback-mode bookkeeping: how far into the script the buffer currently starts, and how
        #: long the last submitted buffer was. See :meth:`_words_for`.
        self._script_position = 0.0
        self._last_seconds = 0.0
        #: Every prompt the engine supplied, so term-biasing behaviour can be asserted.
        self.prompts_seen: list[str | None] = []
        #: Duration in seconds of every array submitted, for buffer-trimming assertions.
        self.audio_seconds_seen: list[float] = []

    # -- identity ------------------------------------------------------------------

    @property
    def backend_id(self) -> str:
        """Stable identifier used in configuration."""
        return "mock"

    @property
    def model_id(self) -> str:
        """Identifier recorded on segments this backend produces."""
        return self._model_id

    @property
    def capabilities(self) -> AsrCapabilities:
        """What this mock declares it can do."""
        return self._capabilities

    @property
    def is_loaded(self) -> bool:
        """Whether :meth:`load` has been called."""
        return self._loaded

    @property
    def pass_count(self) -> int:
        """How many inference passes have run, for assertions about skipped passes."""
        return self._pass_count

    # -- lifecycle -----------------------------------------------------------------

    def load(self) -> None:
        """Mark the backend ready. Instant, unlike a real model."""
        self._loaded = True

    def unload(self) -> None:
        """Release the backend and reset its pass counter."""
        self._loaded = False
        self._pass_count = 0
        self._script_position = 0.0
        self._last_seconds = 0.0

    def set_script(self, script: MockScript) -> None:
        """Replace the script mid-test, e.g. to switch to degenerate output."""
        self._script = script

    # -- transcription -------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, prompt: str | None = None) -> AsrResult:
        """Return the words the script says correspond to this much audio."""
        if not self._loaded:
            raise RuntimeError("MockAsrBackend.transcribe called before load()")

        self._pass_count += 1
        self.prompts_seen.append(prompt)

        array = np.asarray(audio, dtype=np.float32).ravel()
        seconds = array.size / float(SAMPLE_RATE)
        self.audio_seconds_seen.append(seconds)

        if self._script.inference_seconds > 0:
            time.sleep(self._script.inference_seconds)

        if self._is_silent(array) and not self._script.hallucinate_on_silence:
            return AsrResult(words=[], model_id=self._model_id)

        offset = decode_position(array)
        words = (
            self._words_in_window(offset, seconds)
            if offset is not None
            else self._words_for(seconds)
        )
        return AsrResult(
            words=words,
            language="en",
            inference_seconds=self._script.inference_seconds,
            model_id=self._model_id,
        )

    def _words_in_window(self, start: float, seconds: float) -> list[WordToken]:
        """Return the scripted words that fall inside the submitted audio, as a real model would.

        This is what makes the mock a genuine stand-in. Given the last two seconds of a talk it
        returns the words spoken in those two seconds, not the opening of the script — so a trim
        that miscalculates its offset shows up as wrong text rather than passing quietly.
        """
        script = self._script
        if script.repeat_ngram and self._pass_count > script.repeat_after_pass:
            return self._timed(script.repeat_ngram * 8, seconds)

        step = 1.0 / script.words_per_second
        end = start + seconds

        chosen: list[tuple[str, float, float]] = []
        for index, word in enumerate(script.words):
            word_start = index * step
            word_end = word_start + step
            if word_end <= start or word_start >= end:
                continue
            chosen.append((word, word_start - start, word_end - start))

        if script.revise_last > 0 and chosen:
            # Rewrite the trailing words, exactly as a real model revises its own tail once more
            # context arrives. LocalAgreement must not commit these.
            for i in range(max(0, len(chosen) - script.revise_last), len(chosen)):
                text, begins, ends = chosen[i]
                chosen[i] = (f"{text}{script.revision_suffix}", begins, ends)

        if script.unstable_tail > 0 and chosen:
            # A tail that differs every pass can never reach agreement.
            for i in range(max(0, len(chosen) - script.unstable_tail), len(chosen)):
                text, begins, ends = chosen[i]
                chosen[i] = (f"{text}-{self._pass_count}", begins, ends)

        return [
            WordToken(
                text=text,
                start=round(max(0.0, begins), 4),
                end=round(min(seconds, ends), 4),
                confidence=script.confidence,
            )
            for text, begins, ends in chosen
        ]

    def _words_for(self, seconds: float) -> list[WordToken]:
        """Fallback for audio that carries no position encoding — a real recording.

        Without an encoded position the mock cannot know which slice of the session it holds, so it
        infers progress the same way the audio does: **the buffer shrinking means the engine
        trimmed**, and the trimmed duration is how far the script has advanced. Without this the
        mock returns the opening of its script forever and the transcript never moves past the
        first sentence.
        """
        if seconds < self._last_seconds:
            self._script_position += self._last_seconds - seconds
        self._last_seconds = seconds

        return self._words_in_window(self._script_position, seconds)

    def _timed(self, words: list[str], seconds: float) -> list[WordToken]:
        """Spread ``words`` evenly across the submitted audio, with relative timestamps."""
        if not words:
            return []
        step = seconds / len(words) if seconds > 0 else 1.0 / self._script.words_per_second
        return [
            WordToken(
                text=word,
                start=round(index * step, 4),
                end=round((index + 1) * step, 4),
                confidence=self._script.confidence,
            )
            for index, word in enumerate(words)
        ]

    @staticmethod
    def _is_silent(array: np.ndarray) -> bool:
        """Whether the submitted audio is effectively silent."""
        if array.size == 0:
            return True
        return float(np.max(np.abs(array))) < 1e-4


def default_script() -> MockScript:
    """The script the registry's mock backend uses when nothing else is set."""
    return MockScript(words=DEFAULT_SCRIPT_TEXT.split(), words_per_second=2.6)


def scripted(text: str, **kwargs: object) -> MockAsrBackend:
    """Build a loaded mock from a sentence. The common case in tests."""
    backend = MockAsrBackend(MockScript(words=text.split(), **kwargs))  # type: ignore[arg-type]
    backend.load()
    return backend
