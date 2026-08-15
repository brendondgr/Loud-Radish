"""Rolling summaries and glossary extraction while a talk is running.

The behaviour worth holding here is what happens when the language model is *not* available, which
on a machine set up for transcription and nothing else is the normal case. Summarisation stopping
is acceptable; a summarisation failure reaching the pipeline is not.
"""

from __future__ import annotations

import pytest
from app.config.defaults import default_config
from app.models.segment import Segment
from app.services.context.worker import SUMMARY_TOKENS, ContextWorker, parse_terms
from app.services.llm.contract import LlmChunk
from app.services.llm.errors import LlmUnreachableError
from app.services.transcript import TranscriptStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ScriptedBackend:
    """Returns canned completions, or raises.

    Implements ``stream`` rather than ``complete`` because that is the path the worker takes — it
    needs the finish reason, which a collected completion throws away.
    """

    def __init__(self, replies=None, error=None, finish_reason="stop"):
        self._replies = list(replies or [])
        self._error = error
        self._finish_reason = finish_reason
        self.calls: list[str] = []
        self.budgets: list[int] = []

    async def _generate(self, messages, options):
        if self._error:
            raise self._error
        self.calls.append(messages[-1].content)
        self.budgets.append(options.max_output_tokens if options else 0)
        reply = self._replies.pop(0) if self._replies else ""
        if reply:
            yield LlmChunk(text=reply)
        yield LlmChunk(done=True, finish_reason=self._finish_reason)

    def stream(self, messages, options=None):
        return self._generate(messages, options)


@pytest.fixture
def store(tmp_path):
    with TranscriptStore(tmp_path / "session.db") as store:
        yield store


def fill(store, count: int = 12, words: int = 20) -> None:
    """Enough transcript that the worker considers it worth summarising."""
    for index in range(count):
        store.append_segment(
            Segment(
                id=index + 1,
                text=" ".join(f"word{index}_{n}" for n in range(words)),
                start=index * 10.0,
                end=index * 10.0 + 9.0,
            )
        )


def build(store, backend, clock=120.0, **context):
    config = default_config()
    for key, value in context.items():
        setattr(config.context, key, value)
    events: list[tuple[str, dict]] = []
    worker = ContextWorker(
        store=store,
        config=config,
        backend_factory=lambda: backend,
        emit=lambda name, payload: events.append((name, payload)),
        clock=lambda: clock,
    )
    return worker, events


# -- summarising ------------------------------------------------------------------------


async def test_a_summary_is_stored_and_announced(store) -> None:
    fill(store)
    backend = ScriptedBackend(replies=["The speaker set up the counterexample."])
    worker, events = build(store, backend)

    await worker.summarise_pending()

    assert [summary.text for summary in store.summaries()] == [
        "The speaker set up the counterexample."
    ]
    assert events[0][0] == "summary.added"
    assert events[0][1]["end"] == 120.0


async def test_the_cursor_advances_so_the_next_summary_covers_new_material(store) -> None:
    fill(store)
    backend = ScriptedBackend(replies=["first", "second"])
    worker, _ = build(store, backend)

    await worker.summarise_pending()
    assert worker.cursor == 120.0


async def test_too_little_speech_is_not_summarised(store) -> None:
    """A summary of two sentences is longer than the sentences."""
    fill(store, count=1, words=5)
    worker, events = build(store, ScriptedBackend(replies=["unused"]), clock=10.0)

    await worker.summarise_pending()

    assert store.summaries() == []
    assert events == []


async def test_even_the_final_pass_skips_a_fragment(store) -> None:
    """Asked to summarise a dozen words a model returns nothing, which then looks like a failure."""
    fill(store, count=1, words=5)
    worker, events = build(store, ScriptedBackend(replies=["unused"]), clock=10.0)

    await worker.summarise_pending(final=True)

    assert store.summaries() == []
    assert events == []


async def test_the_final_pass_summarises_a_short_tail_anyway(store) -> None:
    """ "Too short to bother with" is not true of a talk's conclusions."""
    fill(store, count=2, words=15)
    worker, _ = build(store, ScriptedBackend(replies=["And that concludes it."]), clock=25.0)

    await worker.summarise_pending(final=True)

    assert [s.text for s in store.summaries()] == ["And that concludes it."]


async def test_a_model_failure_leaves_the_cursor_alone_so_the_stretch_is_retried(store) -> None:
    """Advancing past a failed window would leave a permanent hole in the outline."""
    fill(store)
    worker, events = build(store, ScriptedBackend(error=LlmUnreachableError("nothing there")))

    await worker.summarise_pending()

    assert worker.cursor == 0.0
    assert store.summaries() == []
    assert events[0][0] == "error"
    assert events[0][1]["transcription_continues"] is True
    assert events[0][1]["severity"] == "warning"


async def test_a_persistent_failure_is_reported_once_not_every_interval(store) -> None:
    """Otherwise a model that is simply not running produces a banner every five minutes."""
    fill(store)
    worker, events = build(store, ScriptedBackend(error=LlmUnreachableError("nothing there")))

    await worker.summarise_pending()
    await worker.summarise_pending()
    await worker.summarise_pending()

    assert len([e for e in events if e[0] == "error"]) == 1


# -- glossary ---------------------------------------------------------------------------


async def test_terms_are_extracted_and_announced(store) -> None:
    fill(store)
    backend = ScriptedBackend(
        replies=[
            "a summary",
            "deficiency indices :: how far an operator is from being self-adjoint\n"
            "dilation generator :: the operator generating rescalings of the half line",
        ]
    )
    worker, events = build(store, backend)

    await worker.summarise_pending()

    terms = {term.term for term in store.glossary()}
    assert terms == {"deficiency indices", "dilation generator"}
    assert [name for name, _ in events].count("glossary.added") == 2


async def test_a_term_already_known_is_not_announced_again(store) -> None:
    fill(store)
    store.add_glossary_term("deficiency indices", "already defined", 0.0)
    backend = ScriptedBackend(replies=["a summary", "deficiency indices :: a second definition"])
    worker, events = build(store, backend)

    await worker.summarise_pending()

    assert [name for name, _ in events].count("glossary.added") == 0


async def test_a_glossary_failure_does_not_lose_the_summary(store) -> None:
    """The two are separate calls; one failing must not discard the other's result."""
    fill(store)

    class SummaryOnly(ScriptedBackend):
        async def _generate(self, messages, options):
            if "Summarise" not in messages[0].content:
                raise LlmUnreachableError("gone")
            yield LlmChunk(text="the summary")
            yield LlmChunk(done=True, finish_reason="stop")

    worker, _ = build(store, SummaryOnly())
    await worker.summarise_pending()

    assert [s.text for s in store.summaries()] == ["the summary"]


# -- parsing ----------------------------------------------------------------------------


def test_a_preamble_costs_nothing() -> None:
    """Models add one about one time in ten however firmly they are told not to."""
    raw = (
        "Here are the terms introduced in this passage:\n\n"
        "- Hilbert space :: a complete inner product space\n"
        "essential self-adjointness :: having exactly one self-adjoint extension\n"
        "\nThat is all of them."
    )

    assert parse_terms(raw) == [
        ("Hilbert space", "a complete inner product space"),
        ("essential self-adjointness", "having exactly one self-adjoint extension"),
    ]


def test_an_empty_reply_yields_nothing() -> None:
    assert parse_terms("") == []
    assert parse_terms("This passage introduces no new terminology.") == []


def test_an_implausibly_long_term_is_rejected() -> None:
    """A model that starts writing prose in the term slot is not producing a glossary."""
    raw = f"{'x' * 80} :: a definition"
    assert parse_terms(raw) == []


async def test_an_all_reasoning_summary_is_reported_and_the_stretch_is_kept(store) -> None:
    """A silent skip leaves a gap in the outline that raising the setting cannot then fill."""
    fill(store)
    backend = ScriptedBackend(replies=[""], finish_reason="length")
    worker, events = build(store, backend)

    await worker.summarise_pending()

    assert store.summaries() == []
    assert worker.cursor == 0.0, "the stretch must be retried once the budget is raised"
    assert "Longest answer" in events[0][1]["message"]


async def test_the_output_budget_has_a_floor_generous_enough_for_a_reasoning_model(store) -> None:
    """Measured: a 400-token budget produced nothing but reasoning on every run."""
    fill(store)
    backend = ScriptedBackend(replies=["a summary"])
    worker, _ = build(store, backend)

    await worker.summarise_pending()

    assert backend.budgets[0] >= SUMMARY_TOKENS
