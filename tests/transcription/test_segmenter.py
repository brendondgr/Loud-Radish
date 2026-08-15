"""Grouping committed words into readable segments (BE §7.7)."""

from __future__ import annotations

import pytest
from app.services.asr.contract import WordToken
from app.services.streaming import Segmenter, ends_sentence


def words(text: str, start: float = 0.0, step: float = 0.4) -> list[WordToken]:
    return [
        WordToken(text=token, start=start + i * step, end=start + (i + 1) * step)
        for i, token in enumerate(text.split())
    ]


class TestSentenceDetection:
    @pytest.mark.parametrize(
        "token", ["end.", "really?", "stop!", "trailing…", 'quote."', "close.)"]
    )
    def test_terminators_end_a_sentence(self, token: str) -> None:
        assert ends_sentence(token)

    @pytest.mark.parametrize("token", ["matrix", "the,", "half-", "", "   "])
    def test_ordinary_words_do_not(self, token: str) -> None:
        assert not ends_sentence(token)

    @pytest.mark.parametrize("token", ["Dr.", "e.g.", "etc.", "vs.", "approx."])
    def test_abbreviations_do_not_end_a_sentence(self, token: str) -> None:
        """Splitting on "Dr." would break a paragraph mid-name."""
        assert not ends_sentence(token)

    def test_a_single_initial_does_not_end_a_sentence(self) -> None:
        """A deliberate tradeoff: "J. Smith" is far more common in speech than a sentence
        ending in a single letter, and the cost of guessing wrong is asymmetric — a missed
        boundary makes one paragraph long, an eager one splits a name in half."""
        assert not ends_sentence("J.")
        assert not ends_sentence("b.")


class TestSegmentation:
    def test_a_full_stop_completes_a_segment(self) -> None:
        segmenter = Segmenter()
        segments = segmenter.add(words("the operators commute here."))
        assert len(segments) == 1
        assert segments[0].text == "the operators commute here."

    def test_words_without_a_boundary_stay_pending(self) -> None:
        segmenter = Segmenter()
        assert segmenter.add(words("the operators commute")) == []
        assert segmenter.has_pending

    def test_a_pause_event_completes_a_segment(self) -> None:
        """A speaker who pauses has finished a thought, even without punctuation."""
        segmenter = Segmenter()
        segments = segmenter.add(words("no punctuation here"), pause_boundary=True)
        assert len(segments) == 1
        assert segments[0].text == "no punctuation here"

    def test_a_pause_with_nothing_pending_emits_nothing(self) -> None:
        assert Segmenter().add([], pause_boundary=True) == []

    def test_the_duration_cap_breaks_an_unpunctuated_monologue(self) -> None:
        """Otherwise an hour of unpunctuated speech becomes one wall of text."""
        segmenter = Segmenter(max_segment_s=5.0)
        segments = segmenter.add(words("one two three four five six seven eight", step=1.0))
        assert len(segments) >= 1
        assert all(segment.duration <= 6.0 for segment in segments)

    def test_several_sentences_in_one_batch_produce_several_segments(self) -> None:
        segmenter = Segmenter()
        segments = segmenter.add(words("first one. second one. third one."))
        assert [segment.text for segment in segments] == [
            "first one.",
            "second one.",
            "third one.",
        ]

    def test_ids_are_monotonic(self) -> None:
        """The frontend anchors scrolling on these, so they must never be reassigned."""
        segmenter = Segmenter(first_id=7)
        segments = segmenter.add(words("one. two. three."))
        assert [segment.id for segment in segments] == [7, 8, 9]
        assert segmenter.next_id == 10

    def test_segment_times_span_its_words(self) -> None:
        segmenter = Segmenter()
        segment = segmenter.add(words("alpha beta gamma.", start=10.0, step=0.5))[0]
        assert segment.start == pytest.approx(10.0)
        assert segment.end == pytest.approx(11.5)

    def test_the_model_id_is_recorded(self) -> None:
        """Provenance matters when models are swapped mid-session."""
        segmenter = Segmenter()
        segment = segmenter.add(words("hello."), model_id="faster-whisper:small:int8")[0]
        assert segment.model_id == "faster-whisper:small:int8"

    def test_confidence_averages_across_words(self) -> None:
        segmenter = Segmenter()
        tokens = [
            WordToken(text="spectrum", start=0.0, end=0.2, confidence=0.9),
            WordToken(text="discrete.", start=0.2, end=0.4, confidence=0.7),
        ]
        assert segmenter.add(tokens)[0].confidence == pytest.approx(0.8)

    def test_confidence_is_none_when_no_word_reports_one(self) -> None:
        """A fabricated average would underline text the model was sure about."""
        assert Segmenter().add(words("no scores here."))[0].confidence is None


class TestFlushAndReset:
    def test_flush_emits_the_pending_words(self) -> None:
        """So the last words spoken are not lost waiting for a full stop that never arrives."""
        segmenter = Segmenter()
        segmenter.add(words("trailing words with no stop"))
        final = segmenter.flush()
        assert final is not None
        assert final.text == "trailing words with no stop"

    def test_flush_with_nothing_pending_returns_none(self) -> None:
        assert Segmenter().flush() is None

    def test_flush_clears_the_pending_words(self) -> None:
        segmenter = Segmenter()
        segmenter.add(words("some words"))
        segmenter.flush()
        assert not segmenter.has_pending

    def test_reset_discards_pending_words_and_can_restart_ids(self) -> None:
        segmenter = Segmenter(first_id=5)
        segmenter.add(words("discard me"))
        segmenter.reset(first_id=1)

        assert not segmenter.has_pending
        assert segmenter.next_id == 1

    def test_the_duration_cap_is_a_live_setting(self) -> None:
        segmenter = Segmenter(max_segment_s=60.0)
        segmenter.update_max_duration(1.0)
        assert segmenter.add(words("one two three", step=1.0))


class TestSegmentModel:
    def test_a_segment_serialises_for_the_transport_event(self) -> None:
        segment = Segmenter().add(words("hello there."), model_id="mock:scripted")[0]
        payload = segment.as_event()

        assert set(payload) == {
            "id",
            "text",
            "start",
            "end",
            "wall_clock",
            "confidence",
            "model_id",
            "speaker",
            # Which transcription pass produced it (D-022). Always 0 on the live path.
            "revision",
        }
        assert payload["speaker"] is None

    def test_word_detail_is_optional(self) -> None:
        segment = Segmenter().add(words("hello there."))[0]
        assert "words" not in segment.as_event()
        assert len(segment.as_event(include_words=True)["words"]) == 2

    def test_word_count_reads_from_the_text(self) -> None:
        assert Segmenter().add(words("one two three."))[0].word_count == 3
