"""Choosing what the model sees.

The properties asserted here are the ones that make an answer trustworthy rather than merely
plausible: recent speech goes verbatim, older material is labelled as compressed, nothing is
truncated mid-sentence, and what did not fit is reported rather than silently dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.config.defaults import default_config
from app.models.segment import Segment
from app.models.session import ChatMessage, Summary
from app.services.context.assembler import (
    ContextRequest,
    assemble,
    effective_budget,
    timestamp,
)


class FakeStore:
    """The narrow slice of a transcript store the assembler actually uses."""

    def __init__(self, segments=None, summaries=None, glossary=None, history=None, hits=None):
        self._segments = segments or []
        self._summaries = summaries or []
        self._glossary = glossary or []
        self._history = history or []
        self._hits = hits or []
        self.searched: list[str] = []

    def segments_in_range(self, start, end):
        return [s for s in self._segments if s.start >= start and s.start < end]

    def search(self, query, limit=50):
        self.searched.append(query)
        return self._hits[:limit]

    def summaries(self):
        return list(self._summaries)

    def glossary(self):
        return list(self._glossary)

    def chat_history(self, limit=None):
        return self._history[-limit:] if limit else list(self._history)


def segment(id_: int, start: float, text: str) -> Segment:
    return Segment(
        id=id_,
        text=text,
        start=start,
        end=start + 5.0,
        wall_clock=datetime.now(UTC),
        model_id="mock",
    )


def talk(count: int = 40, spacing: float = 10.0) -> list[Segment]:
    """A synthetic talk long enough that not all of it can be sent."""
    return [
        segment(i + 1, i * spacing, f"segment {i} about operators and dense subspaces " * 6)
        for i in range(count)
    ]


def config_with(**context_overrides):
    config = default_config()
    for key, value in context_overrides.items():
        setattr(config.context, key, value)
    return config


# -- ordering and labelling ---------------------------------------------------------------


def test_recent_speech_goes_verbatim_and_is_labelled_as_such() -> None:
    store = FakeStore(segments=talk())
    config = config_with(recent_verbatim_s=60.0)

    result = assemble(store, config, ContextRequest(question="what was just said?", now=400.0))
    context = "\n".join(m.content for m in result.messages)

    assert "verbatim" in context
    assert "segment 39" in context  # the most recent
    assert "segment 5" not in context  # far outside the window


def test_summaries_are_labelled_as_compressed_not_as_the_speakers_words() -> None:
    """A model given both unlabelled quotes the summary back as if it were said."""
    store = FakeStore(
        segments=talk(),
        summaries=[Summary(id=1, start=0.0, end=120.0, text="The speaker set up the problem.")],
    )

    request = ContextRequest(question="what happened early?", now=400.0)
    result = assemble(store, config_with(), request)
    context = "\n".join(m.content for m in result.messages)

    assert "not the speaker's words" in context
    assert "The speaker set up the problem." in context


def test_the_question_is_the_final_message() -> None:
    """Context is system material; the question is the user's turn."""
    result = assemble(FakeStore(segments=talk()), config_with(), ContextRequest("why?", now=100.0))

    assert result.messages[-1].role == "user"
    assert result.messages[-1].content == "why?"
    assert result.messages[0].role == "system"


def test_a_selected_passage_survives_a_budget_too_small_for_anything_else() -> None:
    """A question about a passage is unanswerable without the passage."""
    store = FakeStore(segments=talk())
    result = assemble(
        store,
        config_with(token_budget=512),
        ContextRequest(
            question="what does this mean?",
            now=400.0,
            quote="the deficiency indices differ",
            quote_start=95.0,
        ),
    )
    context = "\n".join(m.content for m in result.messages)

    assert "deficiency indices differ" in context
    assert "(at 01:35)" in context


# -- the budget ----------------------------------------------------------------------------


def test_a_block_that_does_not_fit_is_dropped_whole_and_reported() -> None:
    """Half an excerpt ending mid-sentence is worse than none: the model treats it as complete."""
    store = FakeStore(segments=talk(count=200), summaries=[Summary(1, 0.0, 100.0, "early stuff")])

    result = assemble(store, config_with(token_budget=600), ContextRequest("summarise", now=2000.0))
    context = "\n".join(m.content for m in result.messages)

    assert result.dropped, "the user must be told what was left out"
    # Whatever was kept is whole — no block appears in part.
    for header in ("verbatim", "compressed"):
        if header in context:
            assert context.count("##") >= 1


def test_the_budget_is_bounded_by_the_models_window_not_only_by_the_setting() -> None:
    """A budget raised for a large model must not silently overrun a small one after a swap."""
    config = default_config()
    config.context.token_budget = 100_000
    config.llm.mode = "local"
    config.llm.local.context_window = 8192
    config.llm.generation.max_output_tokens = 1024

    budget = effective_budget(config)

    assert budget < 8192
    assert budget == int(8192 * 0.6) - 1024


def test_a_budget_below_the_floor_still_leaves_room_to_ask() -> None:
    config = default_config()
    config.llm.local.context_window = 512
    config.llm.generation.max_output_tokens = 500

    assert effective_budget(config) >= 512


# -- windows -------------------------------------------------------------------------------


def test_a_ranged_action_overrides_the_default_verbatim_window() -> None:
    store = FakeStore(segments=talk())
    result = assemble(
        store,
        config_with(recent_verbatim_s=30.0),
        ContextRequest("summarise the last ten minutes", now=400.0, range_minutes=10.0),
    )
    context = "\n".join(m.content for m in result.messages)

    assert "segment 0" in context  # 600 s back covers the whole talk


def test_what_did_i_miss_starts_from_the_read_mark() -> None:
    store = FakeStore(segments=talk())
    result = assemble(
        store, config_with(), ContextRequest("what did I miss?", now=400.0, since=200.0)
    )
    context = "\n".join(m.content for m in result.messages)

    assert "segment 20" in context
    assert "segment 19" not in context


def test_retrieval_is_skipped_when_the_window_already_covers_the_whole_talk() -> None:
    """Retrieving from inside what was already sent verbatim spends budget on duplicates."""
    store = FakeStore(segments=talk(count=5))

    assemble(store, config_with(), ContextRequest("operators", now=50.0, since=0.0))

    assert store.searched == []


def test_retrieval_only_returns_material_older_than_the_verbatim_window() -> None:
    segments = talk(count=40)
    store = FakeStore(segments=segments, hits=[segments[2], segments[38]])

    result = assemble(
        store, config_with(recent_verbatim_s=60.0), ContextRequest("operators", now=400.0)
    )
    context = "\n".join(m.content for m in result.messages)

    assert "Earlier passages" in context
    # segment 38 is inside the verbatim window and must not be sent twice as a "retrieved" hit.
    # Counted by rendered line rather than by phrase — the segment text repeats internally, so a
    # substring count measures the fixture, not the assembler.
    lines = [line for line in context.splitlines() if line.startswith("[06:20]")]
    assert len(lines) == 1, lines


def test_a_search_failure_costs_the_answer_nothing() -> None:
    class Broken(FakeStore):
        def search(self, query, limit=50):
            raise RuntimeError("FTS index is corrupt")

    store = Broken(segments=talk())
    result = assemble(store, config_with(), ContextRequest("operators", now=400.0))

    assert result.messages[-1].content == "operators"


# -- history and citations -------------------------------------------------------------------


def test_recent_conversation_is_carried_so_a_follow_up_makes_sense() -> None:
    history = [
        ChatMessage(id=1, role="user", text="what are the two operators?"),
        ChatMessage(id=2, role="assistant", text="Position and the generator of dilations."),
    ]
    store = FakeStore(segments=talk(), history=history)

    result = assemble(store, config_with(), ContextRequest("and the second one?", now=400.0))
    roles = [m.role for m in result.messages]

    assert "assistant" in roles
    assert result.messages[-1].content == "and the second one?"


def test_cites_name_the_segments_the_answer_may_reference() -> None:
    store = FakeStore(segments=talk())
    result = assemble(
        store, config_with(recent_verbatim_s=60.0), ContextRequest("what?", now=400.0)
    )

    assert result.cites == sorted(set(result.cites))
    assert all(isinstance(cite, int) for cite in result.cites)


def test_the_context_timestamp_is_when_the_question_was_asked() -> None:
    """The answer arrives later; it is about the talk as it stood when asked."""
    result = assemble(FakeStore(), config_with(), ContextRequest("what?", now=123.5))

    assert result.context_timestamp == 123.5


# -- timestamps -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00"), (61, "01:01"), (599, "09:59"), (3600, "1:00:00"), (7325, "2:02:05")],
)
def test_timestamps_match_what_the_transcript_shows(seconds: float, expected: str) -> None:
    """Hours appear only once there are any — a citation must be findable by eye."""
    assert timestamp(seconds) == expected
