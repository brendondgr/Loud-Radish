"""Discarding text the speech model invented rather than heard.

The reported symptom: a transcript that says "thank you", "bye", and stray single words over room
noise and silence. Reproduced on a synthetic tone fixture, which contains no speech and produced a
segment reading "you".

**The test that matters most in this file is not any of the suppression cases.** It is
:meth:`TestThePhraseList.test_a_real_thank_you_inside_a_sentence_survives`, and its siblings: every
filter here can in principle delete something someone said, and a filter that quietly removes real
speech is a worse bug than the one it was written to fix.
"""

from __future__ import annotations

import numpy as np
import pytest
from app.config.schema import AsrConfig, HallucinationConfig
from app.services.asr import AsrLifecycle, AsrResult, MockAsrBackend, MockScript, WordToken
from app.services.asr.hallucination import HALLUCINATION_PHRASES, judge, normalise_phrase
from app.services.audio.formats import SAMPLE_RATE

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def result(
    text: str,
    no_speech: float | None = None,
    logprob: float | None = None,
    confidence: float | None = None,
    seconds: float = 2.0,
) -> AsrResult:
    """A pass with the given words and the model's own opinion of them."""
    words = text.split()
    step = seconds / len(words) if words else 0.0
    return AsrResult(
        words=[
            WordToken(text=word, start=index * step, end=(index + 1) * step, confidence=confidence)
            for index, word in enumerate(words)
        ],
        no_speech_prob=no_speech,
        avg_logprob=logprob,
    )


def config(**overrides: object) -> HallucinationConfig:
    return HallucinationConfig(**overrides)  # type: ignore[arg-type]


class TestTheModelsOwnVerdict:
    """The primary signal. Whisper scores non-speech near 1.0, then transcribes it anyway."""

    def test_a_pass_the_model_calls_non_speech_is_dropped(self) -> None:
        verdict = judge(result("thank you", no_speech=0.95, logprob=-1.8), 2.0, config())
        assert verdict is not None
        assert verdict.code == "no-speech"
        assert "95%" in verdict.reason

    def test_confident_speech_is_kept(self) -> None:
        speech = result("the operators commute", no_speech=0.01, logprob=-0.2)
        assert judge(speech, 2.0, config()) is None

    def test_a_high_score_alone_is_not_enough_when_the_model_was_sure_of_the_words(self) -> None:
        """Corroboration matters in the ordinary band: a decisive decode is often real speech."""
        decisive = result("the operators commute", no_speech=0.7, logprob=-0.1)
        assert judge(decisive, 2.0, config()) is None

    def test_an_all_but_certain_score_needs_no_corroboration(self) -> None:
        """The measured case. `tiny` invented "Oh" at 0.901 with a log-probability of -0.99, which
        the corroborated rule acquitted by a hundredth."""
        verdict = judge(result("Oh", no_speech=0.901, logprob=-0.99, seconds=20.0), 20.0, config())
        assert verdict is not None
        assert verdict.code == "no-speech"

    def test_the_certainty_tier_is_configurable(self) -> None:
        measured = result("Oh", no_speech=0.901, logprob=-0.99, seconds=20.0)
        assert judge(measured, 20.0, config(no_speech_certain=0.95)) is None

    def test_the_primary_signal_stands_alone_when_no_logprob_is_reported(self) -> None:
        """Requiring corroboration that is structurally unavailable would switch the check off."""
        verdict = judge(result("thank you", no_speech=0.95), 2.0, config())
        assert verdict is not None
        assert verdict.code == "no-speech"

    def test_a_model_with_no_opinion_never_triggers_it(self) -> None:
        """Absent is not zero. Filtering on a measurement nobody took deletes real words."""
        assert judge(result("some quiet speech"), 2.0, config(drop_phrases_enabled=False)) is None

    def test_the_threshold_is_configurable(self) -> None:
        # Phrases off, so this isolates the threshold rather than also matching the blocklist.
        marginal = result("thank you", no_speech=0.5, logprob=-1.8)
        assert judge(marginal, 2.0, config(drop_phrases_enabled=False)) is None
        assert judge(marginal, 2.0, config(drop_phrases_enabled=False, no_speech_threshold=0.4))


class TestWordConfidence:
    def test_it_is_off_by_default_because_it_punishes_quiet_speakers(self) -> None:
        assert config().min_word_confidence == 0.0
        assert judge(result("mumbled words here", confidence=0.05), 2.0, config()) is None

    def test_when_switched_on_it_drops_a_low_confidence_pass(self) -> None:
        mumbled = result("mumbled words here", confidence=0.1)
        verdict = judge(mumbled, 2.0, config(min_word_confidence=0.4))
        assert verdict is not None
        assert verdict.code == "low-confidence"

    def test_a_confident_pass_survives_it(self) -> None:
        clear = result("clear words here", confidence=0.9)
        assert judge(clear, 2.0, config(min_word_confidence=0.4)) is None

    def test_a_backend_that_reports_no_confidence_is_not_penalised(self) -> None:
        assert judge(result("no scores here"), 2.0, config(min_word_confidence=0.9)) is None


class TestThePhraseList:
    """The crude layer, for what the model's own numbers miss."""

    def test_the_reported_symptom_is_caught(self) -> None:
        for phrase in ("you", "thank you", "bye", "Thanks for watching!"):
            verdict = judge(result(phrase, seconds=1.5), 1.5, config())
            assert verdict is not None, phrase
            assert verdict.code == "known-phrase"

    def test_a_real_thank_you_inside_a_sentence_survives(self) -> None:
        """The case this whole module has to get right. Only a bare, short pass is a match."""
        speech = result("thank you all for coming this afternoon", seconds=3.0)
        assert judge(speech, 3.0, config()) is None

    def test_a_slowly_spoken_thank_you_survives(self) -> None:
        """The same two words over twenty seconds of audio came from a speaker."""
        assert judge(result("thank you", seconds=20.0), 20.0, config()) is None

    def test_punctuation_and_casing_do_not_matter(self) -> None:
        """Whisper varies both between passes over identical audio."""
        assert judge(result("Bye.", seconds=1.0), 1.0, config()) is not None
        assert judge(result("BYE!", seconds=1.0), 1.0, config()) is not None

    def test_it_can_be_switched_off_on_its_own(self) -> None:
        bare = result("thank you", seconds=1.0)
        assert judge(bare, 1.0, config(drop_phrases_enabled=False)) is None

    def test_the_window_is_configurable(self) -> None:
        assert judge(result("bye", seconds=5.0), 5.0, config()) is None
        assert judge(result("bye", seconds=5.0), 5.0, config(max_phrase_seconds=8.0)) is not None

    def test_the_list_holds_only_observed_boilerplate(self) -> None:
        """A common English word here would delete it from real talks."""
        for ordinary in ("the", "yes", "no", "right", "question", "next slide"):
            assert ordinary not in HALLUCINATION_PHRASES

    def test_normalising_strips_punctuation_and_collapses_space(self) -> None:
        assert normalise_phrase("  Thank   you! ") == "thank you"


class TestSwitchingItOff:
    def test_nothing_is_dropped_when_the_filter_is_disabled(self) -> None:
        condemned = result("thank you", no_speech=0.99, logprob=-3.0)
        assert judge(condemned, 1.0, config(enabled=False)) is None

    def test_an_already_empty_pass_is_not_reported_as_suppressed(self) -> None:
        assert judge(AsrResult(words=[], no_speech_prob=0.99), 1.0, config()) is None


class TestThroughTheLifecycle:
    """Where the filter actually runs: the one place holding both the audio and the ASR settings."""

    @staticmethod
    async def lifecycle(script: MockScript, **overrides: object) -> AsrLifecycle:
        tuning = HallucinationConfig(**overrides)  # type: ignore[arg-type]
        settings = AsrConfig(backend="mock", hallucination=tuning)
        life = AsrLifecycle(settings)
        await life.load(settings)
        life._backend = MockAsrBackend(script)  # noqa: SLF001 - injecting the scripted model
        life._backend.load()  # noqa: SLF001
        return life

    async def test_invented_text_never_reaches_the_engine(self) -> None:
        script = MockScript(
            words=["thank", "you"],
            no_speech_prob=0.97,
            avg_logprob=-2.1,
            hallucinate_on_silence=True,
        )
        life = await self.lifecycle(script)

        outcome = life.transcribe(np.zeros(2 * SAMPLE_RATE, dtype=np.float32))

        assert outcome.is_empty()
        assert life.suppressed == 1

    async def test_real_speech_passes_through_untouched(self) -> None:
        script = MockScript(words=["the", "operators", "commute"], no_speech_prob=0.02)
        life = await self.lifecycle(script)

        outcome = life.transcribe(np.full(2 * SAMPLE_RATE, 0.3, dtype=np.float32))

        assert not outcome.is_empty()
        assert life.suppressed == 0

    async def test_the_models_verdict_survives_suppression_for_diagnosis(self) -> None:
        """The words go; the numbers that condemned them stay, so the decision is inspectable."""
        script = MockScript(
            words=["bye"], no_speech_prob=0.99, avg_logprob=-2.5, hallucinate_on_silence=True
        )
        life = await self.lifecycle(script)

        outcome = life.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))

        assert outcome.no_speech_prob == pytest.approx(0.99)
        assert outcome.model_id

    async def test_suppression_counts_accumulate_across_passes(self) -> None:
        script = MockScript(
            words=["you"], no_speech_prob=0.99, avg_logprob=-2.5, hallucinate_on_silence=True
        )
        life = await self.lifecycle(script)

        for _ in range(3):
            life.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))

        assert life.suppressed == 3
