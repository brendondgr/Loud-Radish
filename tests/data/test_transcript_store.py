"""The transcript store — persistence, queries, and search (BE §8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.asr.contract import WordToken
from app.services.transcript import TranscriptStore

TALK = [
    "The central claim is that these operators commute only on a dense subspace.",
    "Outside it the bracket simply is not defined.",
    "Take the position operator on the half line and the dilation generator alongside.",
    "Both are symmetric on smooth compactly supported functions.",
    "Neither is self-adjoint there and the deficiency indices differ.",
]


def segment(
    segment_id: int,
    text: str = "some text",
    start: float | None = None,
    duration: float = 4.0,
    **overrides: object,
) -> Segment:
    begins = start if start is not None else (segment_id - 1) * duration
    fields: dict = {
        "id": segment_id,
        "text": text,
        "start": begins,
        "end": begins + duration,
        "model_id": "mock:scripted",
        **overrides,
    }
    return Segment(**fields)


@pytest.fixture
def store(tmp_path: Path) -> TranscriptStore:
    with TranscriptStore(tmp_path / "session.db") as store:
        yield store


@pytest.fixture
def talk_store(store: TranscriptStore) -> TranscriptStore:
    store.append_segments([segment(i + 1, text) for i, text in enumerate(TALK)])
    return store


class TestAppendAndRead:
    def test_a_segment_round_trips(self, store: TranscriptStore) -> None:
        store.append_segment(segment(1, "the deficiency indices differ", start=12.0))
        stored = store.segment(1)

        assert stored is not None
        assert stored.text == "the deficiency indices differ"
        assert stored.start == pytest.approx(12.0)
        assert stored.model_id == "mock:scripted"

    def test_word_level_detail_round_trips(self, store: TranscriptStore) -> None:
        words = [
            WordToken(text="deficiency", start=12.0, end=12.6, confidence=0.91),
            WordToken(text="indices", start=12.6, end=13.1),
        ]
        store.append_segment(segment(1, "deficiency indices", words=words))

        stored = store.segment(1)
        assert stored is not None
        assert [word.text for word in stored.words] == ["deficiency", "indices"]
        assert stored.words[0].confidence == pytest.approx(0.91)
        assert stored.words[1].confidence is None

    def test_word_detail_can_be_omitted(self, store: TranscriptStore) -> None:
        store.append_segment(
            segment(1, words=[WordToken(text="x", start=0.0, end=0.2)]), store_words=False
        )
        stored = store.segment(1)
        assert stored is not None and stored.words == []

    def test_the_speaker_field_survives_as_none(self, store: TranscriptStore) -> None:
        """Reserved for diarisation; it must round-trip rather than becoming an empty string."""
        store.append_segment(segment(1))
        stored = store.segment(1)
        assert stored is not None and stored.speaker is None

    def test_an_unknown_segment_is_none(self, store: TranscriptStore) -> None:
        assert store.segment(99) is None

    def test_segments_come_back_in_id_order(self, talk_store: TranscriptStore) -> None:
        assert [s.id for s in talk_store.all_segments()] == [1, 2, 3, 4, 5]

    def test_re_appending_the_same_id_does_not_duplicate(self, store: TranscriptStore) -> None:
        """Append-only means a replayed write is harmless, not a second copy."""
        store.append_segment(segment(1, "original"))
        store.append_segment(segment(1, "different"))

        assert len(store.all_segments()) == 1
        stored = store.segment(1)
        assert stored is not None and stored.text == "original"

    def test_appending_nothing_is_harmless(self, store: TranscriptStore) -> None:
        store.append_segments([])
        assert store.all_segments() == []

    def test_the_last_segment_id_is_reported(self, talk_store: TranscriptStore) -> None:
        assert talk_store.last_segment_id() == 5

    def test_an_empty_transcript_reports_zero(self, store: TranscriptStore) -> None:
        assert store.last_segment_id() == 0


class TestCrashSurvival:
    def test_committed_segments_survive_reopening(self, tmp_path: Path) -> None:
        """A crash eighty minutes into a talk must lose at most the last few seconds."""
        path = tmp_path / "session.db"
        store = TranscriptStore(path)
        store.append_segments([segment(i + 1, text) for i, text in enumerate(TALK)])
        # No close(): simulate the process dying mid-session.

        reopened = TranscriptStore(path)
        assert [s.text for s in reopened.all_segments()] == TALK
        reopened.close()

    def test_metadata_survives_reopening(self, tmp_path: Path) -> None:
        path = tmp_path / "session.db"
        metadata = SessionMetadata(session_id="s-1", title="Operator domains", speaker="A. Speaker")
        TranscriptStore(path, metadata=metadata)

        reopened = TranscriptStore(path)
        stored = reopened.metadata()
        assert stored is not None
        assert stored.title == "Operator domains"
        assert stored.speaker == "A. Speaker"
        reopened.close()


class TestQuerySurface:
    def test_segments_since_returns_everything_after_an_id(
        self, talk_store: TranscriptStore
    ) -> None:
        """The reconnection replay path."""
        assert [s.id for s in talk_store.segments_since(2)] == [3, 4, 5]

    def test_segments_since_zero_returns_everything(self, talk_store: TranscriptStore) -> None:
        assert len(talk_store.segments_since(0)) == 5

    def test_segments_since_the_last_id_returns_nothing(
        self, talk_store: TranscriptStore
    ) -> None:
        assert talk_store.segments_since(5) == []

    def test_segments_since_respects_a_limit(self, talk_store: TranscriptStore) -> None:
        assert [s.id for s in talk_store.segments_since(0, limit=2)] == [1, 2]

    def test_a_time_range_returns_overlapping_segments(self, talk_store: TranscriptStore) -> None:
        """Segments run 0-4, 4-8, 8-12, 12-16, 16-20."""
        assert [s.id for s in talk_store.segments_in_range(5.0, 11.0)] == [2, 3]

    def test_a_range_includes_a_segment_straddling_the_boundary(
        self, talk_store: TranscriptStore
    ) -> None:
        """Dropping it would lose the sentence the user is asking about."""
        assert 1 in [s.id for s in talk_store.segments_in_range(3.0, 5.0)]

    def test_an_empty_range_returns_nothing(self, talk_store: TranscriptStore) -> None:
        assert talk_store.segments_in_range(100.0, 200.0) == []


class TestSearch:
    def test_a_word_is_found(self, talk_store: TranscriptStore) -> None:
        assert [s.id for s in talk_store.search("deficiency")] == [5]

    def test_search_is_case_insensitive(self, talk_store: TranscriptStore) -> None:
        assert talk_store.search("DEFICIENCY")

    def test_several_terms_narrow_the_result(self, talk_store: TranscriptStore) -> None:
        assert [s.id for s in talk_store.search("deficiency indices")] == [5]

    def test_a_term_that_appears_nowhere_returns_nothing(
        self, talk_store: TranscriptStore
    ) -> None:
        assert talk_store.search("thermodynamics") == []

    def test_an_empty_query_returns_nothing(self, talk_store: TranscriptStore) -> None:
        assert talk_store.search("   ") == []

    def test_punctuation_in_a_query_does_not_raise(self, talk_store: TranscriptStore) -> None:
        """FTS5 syntax characters are the user's typing, not a query language."""
        for query in ['self-adjoint"', "NOT (", "operators AND", "*", '"']:
            assert isinstance(talk_store.search(query), list)

    def test_a_hyphenated_term_is_searchable(self, talk_store: TranscriptStore) -> None:
        assert [s.id for s in talk_store.search("self-adjoint")] == [5]

    def test_results_respect_the_limit(self, store: TranscriptStore) -> None:
        store.append_segments([segment(i, "operators commute") for i in range(1, 21)])
        assert len(store.search("operators", limit=5)) == 5

    def test_newly_appended_text_is_immediately_searchable(
        self, talk_store: TranscriptStore
    ) -> None:
        """The index is maintained by trigger, so there is no rebuild step to forget."""
        talk_store.append_segment(segment(6, "an entirely novel Kullback-Leibler divergence"))
        assert [s.id for s in talk_store.search("Kullback")] == [6]


class TestMetadata:
    def test_a_store_without_metadata_reports_none(self, store: TranscriptStore) -> None:
        assert store.metadata() is None

    def test_metadata_can_be_updated(self, store: TranscriptStore) -> None:
        store.write_metadata(SessionMetadata(session_id="s-1", title="First"))
        store.write_metadata(SessionMetadata(session_id="s-1", title="Second"))

        stored = store.metadata()
        assert stored is not None and stored.title == "Second"

    def test_a_running_session_has_no_end_time(self, store: TranscriptStore) -> None:
        store.write_metadata(SessionMetadata(session_id="s-1"))
        stored = store.metadata()
        assert stored is not None and stored.is_running

    def test_marking_ended_records_the_time(self, store: TranscriptStore) -> None:
        store.write_metadata(SessionMetadata(session_id="s-1"))
        store.mark_ended(datetime(2026, 8, 14, 12, 0, tzinfo=UTC))

        stored = store.metadata()
        assert stored is not None
        assert not stored.is_running
        assert stored.ended_at == datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

    def test_the_config_snapshot_round_trips(self, store: TranscriptStore) -> None:
        store.write_metadata(
            SessionMetadata(session_id="s-1", config={"asr": {"model": "small"}})
        )
        stored = store.metadata()
        assert stored is not None and stored.config["asr"]["model"] == "small"


class TestContextRecords:
    def test_a_summary_round_trips_with_an_assigned_id(self, store: TranscriptStore) -> None:
        summary = store.add_summary(0.0, 300.0, "He dismantled the assumption that it holds.")
        assert summary.id > 0
        assert [s.text for s in store.summaries()] == [summary.text]

    def test_summaries_come_back_in_time_order(self, store: TranscriptStore) -> None:
        store.add_summary(600.0, 900.0, "third")
        store.add_summary(0.0, 300.0, "first")
        store.add_summary(300.0, 600.0, "second")
        assert [s.text for s in store.summaries()] == ["first", "second", "third"]

    def test_a_glossary_term_round_trips(self, store: TranscriptStore) -> None:
        store.add_glossary_term("Deficiency indices", "How far an operator is from self-adjoint.", 45.0)
        terms = store.glossary()
        assert len(terms) == 1
        assert terms[0].first_seen == pytest.approx(45.0)

    def test_re_adding_a_term_keeps_the_earliest_first_use(self, store: TranscriptStore) -> None:
        """First use is what the frontend jumps to; a later sighting must not overwrite it."""
        store.add_glossary_term("Schwartz space", "Smooth rapidly-decaying functions.", 45.0)
        store.add_glossary_term("Schwartz space", "A better definition.", 900.0)

        terms = store.glossary()
        assert len(terms) == 1
        assert terms[0].first_seen == pytest.approx(45.0)
        assert terms[0].definition == "A better definition."

    def test_glossary_terms_come_back_in_order_of_first_use(self, store: TranscriptStore) -> None:
        store.add_glossary_term("Second", "b", 200.0)
        store.add_glossary_term("First", "a", 100.0)
        assert [t.term for t in store.glossary()] == ["First", "Second"]

    def test_chat_history_round_trips(self, store: TranscriptStore) -> None:
        store.add_chat_message("user", "What does that mean?")
        store.add_chat_message(
            "assistant", "It means the counts differ.", context_timestamp=132.0,
            meta={"cites": [110.0]},
        )

        history = store.chat_history()
        assert [m.role for m in history] == ["user", "assistant"]
        assert history[1].context_timestamp == pytest.approx(132.0)
        assert history[1].meta["cites"] == [110.0]

    def test_chat_history_respects_a_limit_keeping_the_most_recent(
        self, store: TranscriptStore
    ) -> None:
        for i in range(10):
            store.add_chat_message("user", f"question {i}")
        assert [m.text for m in store.chat_history(limit=2)] == ["question 8", "question 9"]

    def test_clearing_chat_leaves_the_transcript_intact(self, talk_store: TranscriptStore) -> None:
        talk_store.add_chat_message("user", "a question")
        talk_store.clear_chat_history()

        assert talk_store.chat_history() == []
        assert len(talk_store.all_segments()) == 5


class TestStats:
    def test_an_empty_store_reports_zeroes(self, store: TranscriptStore) -> None:
        stats = store.stats()
        assert stats.segment_count == 0
        assert stats.word_count == 0
        assert stats.duration_seconds == 0.0

    def test_totals_are_reported(self, talk_store: TranscriptStore) -> None:
        stats = talk_store.stats()
        assert stats.segment_count == 5
        assert stats.duration_seconds == pytest.approx(20.0)
        assert stats.word_count == sum(len(text.split()) for text in TALK)

    def test_context_records_are_counted(self, talk_store: TranscriptStore) -> None:
        talk_store.add_summary(0.0, 10.0, "a summary")
        talk_store.add_glossary_term("a term", "a definition", 1.0)

        stats = talk_store.stats()
        assert stats.summary_count == 1
        assert stats.glossary_count == 1

    def test_stats_serialise(self, talk_store: TranscriptStore) -> None:
        payload = talk_store.stats().as_dict()
        assert set(payload) == {"segments", "words", "duration_s", "summaries", "glossary_terms"}


def test_a_long_session_stays_queryable(tmp_path: Path) -> None:
    """A 90-minute talk is several hundred segments; search must not degrade into a scan."""
    with TranscriptStore(tmp_path / "long.db") as store:
        store.append_segments(
            [
                segment(i, f"segment {i} discussing operators and domains", start=i * 12.0)
                for i in range(1, 500)
            ]
        )

        assert store.stats().segment_count == 499
        assert len(store.search("operators", limit=1000)) == 499
        assert len(store.segments_in_range(1200.0, 1260.0)) == 5
        assert store.segments_since(490)[0].id == 491


def test_wall_clock_is_preserved_across_a_reopen(tmp_path: Path) -> None:
    """So the user can correlate the transcript with their own notes."""
    when = datetime.now(UTC) - timedelta(hours=1)
    path = tmp_path / "session.db"
    with TranscriptStore(path) as store:
        store.append_segment(segment(1, wall_clock=when))

    with TranscriptStore(path) as reopened:
        stored = reopened.segment(1)
        assert stored is not None
        assert abs((stored.wall_clock - when).total_seconds()) < 0.001
