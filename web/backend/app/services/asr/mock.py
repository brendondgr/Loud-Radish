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
        repeat_ngram: when set, degenerate into looping this phrase, reproducing Whisper's
            repetition failure mode so the repetition filter can be tested.
        inference_seconds: simulated inference cost, for real-time factor tests.
    """

    words: list[str] = field(default_factory=list)
    words_per_second: float = 2.5
    revise_last: int = 0
    revision_suffix: str = "'"
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

        words = self._words_for(seconds)
        return AsrResult(
            words=words,
            language="en",
            inference_seconds=self._script.inference_seconds,
            model_id=self._model_id,
        )

    def _words_for(self, seconds: float) -> list[WordToken]:
        """Choose the word list for this pass, applying revision and repetition behaviour."""
        script = self._script

        if script.repeat_ngram and self._pass_count > script.repeat_after_pass:
            return self._timed(script.repeat_ngram * 8, seconds)

        count = min(len(script.words), max(0, int(seconds * script.words_per_second)))
        chosen = list(script.words[:count])

        if script.revise_last > 0 and chosen:
            # Rewrite the trailing words, exactly as a real model revises its own tail once more
            # context arrives. LocalAgreement must not commit these.
            revise_from = max(0, len(chosen) - script.revise_last)
            for i in range(revise_from, len(chosen)):
                chosen[i] = f"{chosen[i]}{script.revision_suffix}"

        return self._timed(chosen, seconds)

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


def scripted(text: str, **kwargs: object) -> MockAsrBackend:
    """Build a loaded mock from a sentence. The common case in tests."""
    backend = MockAsrBackend(MockScript(words=text.split(), **kwargs))  # type: ignore[arg-type]
    backend.load()
    return backend
