"""Seam A — the ASR contract, the mock backend, the registry, and term biasing (BE §6)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from app.config.schema import AsrConfig
from app.services.asr import (
    AsrCapabilities,
    AsrResult,
    AsrUnavailableError,
    MockAsrBackend,
    MockScript,
    PromptBuilder,
    WordToken,
    available_backends,
    build_backend,
    registered_ids,
    scripted,
)
from app.services.audio.formats import SAMPLE_RATE


def audio(seconds: float, amplitude: float = 0.3) -> np.ndarray:
    """Non-silent audio of a given duration."""
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float64) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class _FakeWord(SimpleNamespace):
    pass


def whisper_segment(
    start: float,
    end: float,
    text: str,
    no_speech: float | None = None,
    logprob: float | None = None,
) -> SimpleNamespace:
    """One segment shaped like faster-whisper's, with word-level detail.

    Attributes are omitted rather than set to ``None`` when unspecified, because that is how an
    older library version presents them and the backend has to tell "did not say" from "said zero".
    """
    span = max(end - start, 0.0)
    words = text.split()
    step = span / len(words) if words and span else 0.0
    segment = SimpleNamespace(
        start=start,
        end=end,
        words=[
            _FakeWord(
                word=f" {word}",
                start=start + index * step,
                end=start + (index + 1) * step,
                probability=0.9,
            )
            for index, word in enumerate(words)
        ],
    )
    if no_speech is not None:
        segment.no_speech_prob = no_speech
    if logprob is not None:
        segment.avg_logprob = logprob
    return segment


class TestWordToken:
    def test_duration_is_the_span(self) -> None:
        assert WordToken(text="matrix", start=1.0, end=1.4).duration == pytest.approx(0.4)

    def test_a_reversed_span_reports_zero_rather_than_negative(self) -> None:
        assert WordToken(text="x", start=2.0, end=1.0).duration == 0.0

    def test_confidence_defaults_to_none_rather_than_a_number(self) -> None:
        """A fabricated confidence is worse than none: the UI highlights words based on it."""
        assert WordToken(text="x", start=0.0, end=0.1).confidence is None

    def test_tokens_are_immutable(self) -> None:
        token = WordToken(text="x", start=0.0, end=0.1)
        with pytest.raises(AttributeError):
            token.text = "y"  # type: ignore[misc]


class TestAsrResult:
    def test_text_joins_the_words(self) -> None:
        result = AsrResult(
            words=[
                WordToken(text="the", start=0.0, end=0.2),
                WordToken(text="matrix", start=0.2, end=0.6),
            ]
        )
        assert result.text == "the matrix"

    def test_an_empty_result_reports_itself_as_empty(self) -> None:
        result = AsrResult(words=[])
        assert result.is_empty()
        assert result.text == ""
        assert result.audio_end == 0.0

    def test_audio_end_is_the_last_word_end(self) -> None:
        result = AsrResult(words=[WordToken(text="x", start=0.0, end=2.5)])
        assert result.audio_end == pytest.approx(2.5)

    def test_the_models_own_confidence_has_no_opinion_by_default(self) -> None:
        """Absent must never read as "definitely speech" — that would delete real words."""
        result = AsrResult(words=[])
        assert result.no_speech_prob is None
        assert result.avg_logprob is None


class TestWhisperConfidenceCollection:
    """The signals that make invented text detectable, read off faster-whisper's own output.

    Exercised against fakes shaped like the library's, because the library and its weights are not
    installed here. That is a real limit and it is why the conversion is pinned down this closely:
    it is the part of the backend that can be wrong without any test noticing.
    """

    @staticmethod
    def collect(segments):
        from app.services.asr.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend._collect(segments)

    def test_the_no_speech_estimate_is_kept_rather_than_discarded(self) -> None:
        words, confidence = self.collect([whisper_segment(0.0, 2.0, "hello", no_speech=0.92)])
        assert [word.text for word in words] == ["hello"]
        assert confidence.no_speech_prob == pytest.approx(0.92)

    def test_the_average_log_probability_is_kept(self) -> None:
        _, confidence = self.collect([whisper_segment(0.0, 2.0, "hello", logprob=-1.4)])
        assert confidence.avg_logprob == pytest.approx(-1.4)

    def test_several_segments_are_weighted_by_how_much_audio_they_cover(self) -> None:
        """A half-second aside must not outvote twenty seconds of speech."""
        _, confidence = self.collect(
            [
                whisper_segment(0.0, 20.0, "a long stretch of real speech", no_speech=0.01),
                whisper_segment(20.0, 20.5, "hm", no_speech=0.99),
            ]
        )
        assert confidence.no_speech_prob < 0.05

    def test_zero_length_segments_still_count(self) -> None:
        _, confidence = self.collect(
            [
                whisper_segment(1.0, 1.0, "a", no_speech=0.2),
                whisper_segment(1.0, 1.0, "b", no_speech=0.8),
            ]
        )
        assert confidence.no_speech_prob == pytest.approx(0.5)

    def test_a_model_that_reports_nothing_leaves_no_opinion(self) -> None:
        _, confidence = self.collect([whisper_segment(0.0, 2.0, "hello")])
        assert confidence.no_speech_prob is None
        assert confidence.avg_logprob is None

    def test_an_unreadable_value_is_no_opinion_rather_than_zero(self) -> None:
        _, confidence = self.collect([whisper_segment(0.0, 2.0, "hi", no_speech=float("nan"))])
        assert confidence.no_speech_prob is None

    def test_an_empty_pass_leaves_no_opinion(self) -> None:
        words, confidence = self.collect([])
        assert words == []
        assert confidence.no_speech_prob is None


class TestCapabilities:
    def test_a_wildcard_language_list_accepts_anything(self) -> None:
        assert AsrCapabilities(languages=["*"]).supports_language("cy")

    def test_a_specific_language_list_is_enforced(self) -> None:
        caps = AsrCapabilities(languages=["en", "de"])
        assert caps.supports_language("de")
        assert not caps.supports_language("cy")

    def test_no_language_requested_is_always_supported(self) -> None:
        assert AsrCapabilities(languages=["en"]).supports_language(None)

    def test_capabilities_serialise_for_the_model_list(self) -> None:
        payload = AsrCapabilities().as_dict()
        assert set(payload) == {
            "word_timestamps",
            "streaming_native",
            "accepts_prompt",
            "languages",
            "max_audio_seconds",
            "runs_on",
            "confidence",
        }


class TestMockBackend:
    def test_transcribing_before_load_is_an_error(self) -> None:
        with pytest.raises(RuntimeError, match="before load"):
            MockAsrBackend(MockScript(words=["a"])).transcribe(audio(1.0))

    def test_it_returns_more_words_as_more_audio_arrives(self) -> None:
        """The growing buffer is the whole premise of the streaming engine."""
        backend = scripted("the eigenvalue spectrum of the closure is discrete")
        assert len(backend.transcribe(audio(1.0)).words) < len(backend.transcribe(audio(3.0)).words)

    def test_timestamps_are_relative_to_the_submitted_array(self) -> None:
        """The single most important property of the contract (BE §6.2)."""
        backend = scripted("one two three four")
        result = backend.transcribe(audio(4.0))

        assert result.words[0].start == pytest.approx(0.0)
        assert result.audio_end <= 4.0 + 1e-6

    def test_timestamps_stay_relative_across_repeated_passes(self) -> None:
        """A backend must never try to be helpful and return session-absolute times."""
        backend = scripted("one two three four five six")
        for _ in range(5):
            assert backend.transcribe(audio(3.0)).words[0].start == pytest.approx(0.0)

    def test_words_are_ordered_and_non_overlapping(self) -> None:
        words = scripted("alpha beta gamma delta").transcribe(audio(4.0)).words
        for earlier, later in zip(words, words[1:], strict=False):
            assert earlier.end <= later.start + 1e-6

    def test_silence_produces_nothing_by_default(self) -> None:
        assert scripted("some words here").transcribe(silence(3.0)).is_empty()

    def test_the_hallucination_failure_mode_can_be_reproduced(self) -> None:
        """The silence gate exists because real autoregressive models do exactly this."""
        backend = MockAsrBackend(
            MockScript(words=["thanks", "for", "watching"], hallucinate_on_silence=True)
        )
        backend.load()
        assert not backend.transcribe(silence(3.0)).is_empty()

    def test_the_revision_behaviour_rewrites_the_tail(self) -> None:
        """ "matrix" becoming "the matrix" between passes is what LocalAgreement absorbs."""
        backend = MockAsrBackend(MockScript(words=["a", "b", "c", "d"], revise_last=2))
        backend.load()
        texts = [word.text for word in backend.transcribe(audio(2.0)).words]
        assert texts[0] == "a"
        assert texts[-1].endswith("'")

    def test_the_repetition_failure_mode_can_be_reproduced(self) -> None:
        """Whisper's degenerate looping, so the repetition filter can be tested."""
        backend = MockAsrBackend(
            MockScript(words=["fine"], repeat_ngram=["and", "so"], repeat_after_pass=1)
        )
        backend.load()
        backend.transcribe(audio(2.0))
        text = backend.transcribe(audio(2.0)).text
        assert text.count("and so") > 3

    def test_it_records_the_prompts_it_was_given(self) -> None:
        backend = scripted("hello world")
        backend.transcribe(audio(2.0), prompt="Kullback-Leibler divergence")
        assert backend.prompts_seen == ["Kullback-Leibler divergence"]

    def test_it_records_the_audio_durations_it_was_given(self) -> None:
        """Buffer-trim assertions depend on knowing what the engine actually submitted."""
        backend = scripted("hello world")
        backend.transcribe(audio(2.0))
        backend.transcribe(audio(0.5))
        assert backend.audio_seconds_seen == pytest.approx([2.0, 0.5])

    def test_unload_resets_the_pass_counter(self) -> None:
        backend = scripted("a b c")
        backend.transcribe(audio(1.0))
        backend.unload()
        assert not backend.is_loaded
        assert backend.pass_count == 0

    def test_it_describes_itself_for_the_model_list(self) -> None:
        payload = scripted("a").describe()
        assert payload["backend"] == "mock"
        assert payload["loaded"] is True


class TestRegistry:
    def test_both_built_in_backends_are_registered(self) -> None:
        assert set(registered_ids()) >= {"mock", "faster-whisper"}

    def test_an_unknown_backend_lists_what_is_registered(self) -> None:
        with pytest.raises(AsrUnavailableError, match="Registered backends"):
            build_backend(AsrConfig(backend="not-a-backend"))

    def test_the_mock_backend_is_constructible_from_config(self) -> None:
        backend = build_backend(AsrConfig(backend="mock", model="scripted"))
        assert backend.backend_id == "mock"
        assert backend.model_id == "mock:scripted"

    def test_unavailable_backends_are_listed_with_a_remedy(self) -> None:
        """A model that silently does not appear looks like a missing feature."""
        whisper = next(b for b in available_backends() if b.backend_id == "faster-whisper")
        if not whisper.available:
            assert "asr-whisper" in whisper.unavailable_reason

    def test_every_backend_offers_models_to_choose_between(self) -> None:
        for info in available_backends():
            assert info.models, f"{info.backend_id} offers no models"
            assert all("name" in model for model in info.models)

    def test_backend_info_serialises(self) -> None:
        payload = available_backends()[0].as_dict()
        assert set(payload) >= {"backend", "name", "available", "capabilities", "models"}


class TestPromptBuilder:
    def test_no_prompt_when_both_mechanisms_are_disabled(self) -> None:
        """``None``, not an empty string: some backends treat those differently."""
        config = AsrConfig(use_session_prompt=False, use_rolling_prompt=False)
        assert PromptBuilder(config).build("some committed text") is None

    def test_the_session_prompt_is_included(self) -> None:
        config = AsrConfig(session_prompt="Self-adjoint extensions of unbounded operators")
        assert "Self-adjoint" in (PromptBuilder(config).build() or "")

    def test_rolling_context_uses_the_tail_of_committed_text(self) -> None:
        config = AsrConfig(session_prompt="", rolling_prompt_chars=40)
        prompt = PromptBuilder(config).build("a" * 100 + " the deficiency indices differ")
        assert prompt is not None
        assert prompt.endswith("the deficiency indices differ")

    def test_rolling_context_can_be_disabled_independently(self) -> None:
        config = AsrConfig(session_prompt="abstract text", use_rolling_prompt=False)
        prompt = PromptBuilder(config).build("recent committed words")
        assert prompt == "abstract text"

    def test_glossary_terms_feed_back_into_biasing(self) -> None:
        """BE §9.3: a term recognised once is more likely to keep being recognised."""
        builder = PromptBuilder(AsrConfig(session_prompt="A talk"))
        builder.add_glossary_terms(["Kullback-Leibler", "deficiency indices"])
        prompt = builder.build() or ""
        assert "Kullback-Leibler" in prompt
        assert "deficiency indices" in prompt

    def test_duplicate_glossary_terms_are_ignored(self) -> None:
        builder = PromptBuilder(AsrConfig())
        builder.add_glossary_terms(["Schwartz space", "Schwartz space", " Schwartz space "])
        assert builder.glossary_terms == ["Schwartz space"]

    def test_an_over_long_prompt_is_trimmed_keeping_recent_context(self) -> None:
        """Recent words predict what comes next; the session prompt is background."""
        config = AsrConfig(session_prompt="x" * 2000, rolling_prompt_chars=60)
        prompt = PromptBuilder(config).build("the deficiency indices differ") or ""
        assert len(prompt) <= 800
        assert prompt.endswith("the deficiency indices differ")

    def test_an_empty_session_prompt_contributes_nothing(self) -> None:
        assert PromptBuilder(AsrConfig(session_prompt="   ")).build("") is None

    def test_updating_config_is_reflected_immediately(self) -> None:
        builder = PromptBuilder(AsrConfig(session_prompt="first"))
        builder.update_config(AsrConfig(session_prompt="second"))
        assert builder.build() == "second"


class TestPositionEncoding:
    """The mock's position encoding must be impossible to confuse with a real recording.

    A check on magnitude alone is not enough: loud speech reaches 0.7, which sits inside the
    encoded range and decodes as a plausible timestamp — sending the mock hunting for words
    hundreds of seconds into its script, and producing an empty transcript with no error anywhere.
    """

    def test_encoded_audio_round_trips(self) -> None:
        from app.services.asr.mock import decode_position, positional_audio

        for start in (0.0, 12.5, 240.75):
            decoded = decode_position(positional_audio(start, 0.5))
            assert decoded == pytest.approx(start, abs=0.01)

    def test_a_loud_recording_is_not_mistaken_for_an_encoding(self) -> None:
        from app.services.asr.mock import decode_position

        assert decode_position(audio(1.0, amplitude=0.7)) is None
        assert decode_position(audio(1.0, amplitude=0.55)) is None

    def test_silence_is_not_mistaken_for_an_encoding(self) -> None:
        from app.services.asr.mock import decode_position

        assert decode_position(silence(1.0)) is None

    def test_a_constant_signal_in_range_is_rejected(self) -> None:
        """Non-decreasing, but with no span — it carries no time."""
        from app.services.asr.mock import decode_position

        assert decode_position(np.full(8000, 0.5, dtype=np.float32)) is None

    def test_a_backend_given_a_real_recording_still_produces_words(self) -> None:
        """The failure this guards: the whole pipeline running and committing nothing."""
        backend = scripted("alpha beta gamma delta epsilon zeta eta theta")
        assert not backend.transcribe(audio(3.0, amplitude=0.7)).is_empty()
