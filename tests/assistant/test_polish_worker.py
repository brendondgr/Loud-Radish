"""The background polish pass, driven against a real store and a scripted model.

The behaviour that matters most is what happens when the model is *not* cooperating — missing,
unreachable, or summarising when it was told to tidy. In every one of those cases the required
outcome is identical and specific: no polished block, no event, and the raw segments left exactly
as they are, because the page falling back to what it shows today is the feature working, not the
feature failing.
"""

from __future__ import annotations

import pytest
from app.config.defaults import default_config
from app.models.segment import Segment
from app.services.llm.contract import LlmChunk
from app.services.llm.errors import LlmServerError, LlmUnreachableError
from app.services.polish.prompts import DEFAULT_POLISH_PROMPT
from app.services.polish.worker import MAX_ATTEMPTS, PolishWorker
from app.services.transcript import TranscriptStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ScriptedBackend:
    """Returns canned completions, or raises. Streams, because that is the path the worker takes."""

    def __init__(self, replies=None, error=None, finish_reason="stop", fail_times=0):
        self._replies = list(replies or [])
        self._error = error
        self._finish_reason = finish_reason
        #: Raise for the first N calls only — how a strict server rejecting an unknown body field
        #: is distinguished from a server that is simply down.
        self._fail_times = fail_times
        self.calls: list[str] = []
        self.extras: list[dict] = []
        #: The system message of each call — the instruction list the worker resolved.
        self.systems: list[str] = []

    async def _generate(self, messages, options):
        self.extras.append(dict(options.extra) if options else {})
        if self._fail_times > 0:
            self._fail_times -= 1
            raise LlmServerError("HTTP 400: unknown field 'think'")
        if self._error:
            raise self._error
        self.calls.append(messages[-1].content)
        self.systems.append(messages[0].content)
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


#: One minute of raw transcript, as the speech model produces it: fragments, filler, no punctuation.
RAW = [
    "um so the the first thing to say is that",
    "the measurement was forty two microseconds",
    "which is uh well below the threshold we set",
    "and that holds across all three of the runs",
]

#: The same minute as the model is given it: one continuous run, timestamps already placed. The
#: segments are 16 s apart and the default marker interval is 15 s, so every one of them gets a
#: marker.
MARKED_SOURCE = (
    "[00:00] um so the the first thing to say is that "
    "[00:16] the measurement was forty two microseconds "
    "[00:32] which is uh well below the threshold we set "
    "[00:48] and that holds across all three of the runs"
)

#: What a cooperative model returns: one paragraph, filler gone, markers carried through to the
#: material they belong to.
POLISHED = (
    "[00:00] So the first thing to say is that "
    "[00:16] the measurement was forty two microseconds, "
    "[00:32] which is well below the threshold we set, "
    "[00:48] and that holds across all three of the runs."
)

#: The same, for the tests that only write the first two segments. It has to be the right length —
#: the guard rejects a rewrite that is wildly longer than what it was given, which is the point.
POLISHED_HALF = (
    "[00:00] So the first thing to say is that [00:16] the measurement was forty two microseconds."
)


def fill(store, texts=RAW, seconds_each: float = 16.0) -> None:
    """Write committed segments covering a little over a minute."""
    for index, text in enumerate(texts):
        store.append_segment(
            Segment(
                id=index + 1,
                text=text,
                start=index * seconds_each,
                end=index * seconds_each + seconds_each - 1.0,
            )
        )


def build(store, backend, silence: float = 3.0, **overrides):
    """A worker over ``store``, with settings resolved fresh on every poll."""
    config = default_config()
    for key, value in overrides.items():
        setattr(config.polish, key, value)

    events: list[tuple[str, dict]] = []
    worker = PolishWorker(
        store=store,
        config_provider=lambda: config,
        backend_factory=lambda: backend,
        emit=lambda name, data: events.append((name, data)),
        silence_provider=lambda: silence,
    )
    return worker, events, config


class TestTheHappyPath:
    async def test_a_finished_minute_becomes_one_block_of_prose(self, store) -> None:
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED])
        worker, events, _ = build(store, backend)

        assert await worker.poll() is True

        blocks = store.polished_blocks()
        assert len(blocks) == 1
        assert blocks[0].text == POLISHED
        assert blocks[0].source_ids == [1, 2, 3, 4]
        assert events == [("transcript.polished", blocks[0].as_event())]

    async def test_the_raw_transcript_is_left_untouched(self, store) -> None:
        """The polished text is a layer over the record, never a replacement for it."""
        fill(store)
        worker, _, _ = build(store, ScriptedBackend(replies=[POLISHED]))
        await worker.poll()

        assert [segment.text for segment in store.all_segments()] == RAW

    async def test_the_model_is_sent_one_continuous_run_with_the_timestamps_in_it(
        self, store
    ) -> None:
        """Step one of the pass: rewrite the whole passage, not fragment by fragment."""
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED])
        worker, _, _ = build(store, backend)
        await worker.poll()

        assert backend.calls[0] == MARKED_SOURCE
        assert "\n" not in backend.calls[0]

    async def test_decoration_the_model_added_is_stripped_before_storage(self, store) -> None:
        """The pane renders through textContent, so `**` would reach the reader as asterisks."""
        fill(store)
        decorated = POLISHED.replace("forty two", "**forty two**")
        worker, _, _ = build(store, ScriptedBackend(replies=[decorated]))
        await worker.poll()

        assert store.polished_blocks()[0].text == POLISHED

    async def test_the_next_chunk_starts_where_the_last_one_ended(self, store) -> None:
        fill(store)
        worker, _, _ = build(store, ScriptedBackend(replies=[POLISHED]))
        await worker.poll()
        assert worker.cursor == store.last_segment_end()

        # Nothing new has committed, so a second poll is a no-op rather than a repeat.
        assert await worker.poll() is False
        assert len(store.polished_blocks()) == 1

    async def test_a_reopened_session_does_not_polish_the_same_minutes_twice(self, store) -> None:
        fill(store)
        store.add_polished_block(0.0, 47.0, POLISHED, [1, 2, 3])
        worker, _, _ = build(store, ScriptedBackend(replies=[POLISHED]))

        assert worker.cursor == 47.0


class TestTimestamps:
    """They are what makes a polished stretch traceable back to the moment it was said."""

    async def test_the_markers_the_model_carried_through_are_stored(self, store) -> None:
        fill(store)
        worker, _, _ = build(store, ScriptedBackend(replies=[POLISHED]))
        await worker.poll()

        assert store.polished_blocks()[0].text == POLISHED

    async def test_a_marker_the_model_invented_never_reaches_the_page(self, store) -> None:
        """It would look identical to a real one until the reader clicked it."""
        fill(store)
        invented = POLISHED.replace("[00:32]", "[00:37]")
        worker, _, _ = build(store, ScriptedBackend(replies=[invented]))
        await worker.poll()

        text = store.polished_blocks()[0].text
        assert "[00:37]" not in text
        assert "[00:16]" in text

    async def test_a_model_that_dropped_every_marker_still_leaves_the_block_locatable(
        self, store
    ) -> None:
        fill(store)
        unmarked = " ".join(word for word in POLISHED.split() if not word.startswith("[0"))
        worker, _, _ = build(store, ScriptedBackend(replies=[unmarked]))
        await worker.poll()

        assert store.polished_blocks()[0].text.startswith("[00:00] ")

    async def test_the_interval_controls_how_many_markers_are_offered(self, store) -> None:
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED])
        worker, _, _ = build(store, backend, timestamp_interval_s=60.0)
        await worker.poll()

        assert backend.calls[0].count("[") == 1


class TestOneContinuousParagraph:
    """The requirement: a transcript that breaks to a new line constantly is disruptive to read."""

    async def test_a_model_that_returns_three_paragraphs_stores_one(self, store) -> None:
        fill(store)
        broken = POLISHED.replace(", [00:32]", ".\n\n[00:32]").replace(", [00:48]", ".\n\n[00:48]")
        worker, _, _ = build(store, ScriptedBackend(replies=[broken]))
        await worker.poll()

        assert "\n" not in store.polished_blocks()[0].text

    async def test_a_list_the_speaker_enumerated_keeps_its_shape(self, store) -> None:
        fill(store)
        listed = "[00:00] There were three reasons:\n\n- cost\n- time\n- accuracy and repeatability"
        worker, _, _ = build(store, ScriptedBackend(replies=[listed]), min_retained_ratio=0.0)
        await worker.poll()

        assert store.polished_blocks()[0].text == listed


class TestWaitingForABreak:
    async def test_it_does_not_cut_mid_sentence(self, store) -> None:
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED])
        worker, _, _ = build(store, backend, silence=0.0)

        assert await worker.poll() is False
        assert backend.calls == []

    async def test_it_cuts_once_the_speaker_stops(self, store) -> None:
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED])
        worker, _, _ = build(store, backend, silence=2.5)

        assert await worker.poll() is True

    async def test_the_final_pass_catches_the_end_of_the_talk(self, store) -> None:
        """A talk ending forty seconds into a chunk would otherwise lose its conclusions."""
        fill(store, texts=RAW[:2])
        backend = ScriptedBackend(replies=[POLISHED_HALF])
        worker, _, _ = build(store, backend, silence=0.0)

        assert await worker.poll() is False
        assert await worker.poll(final=True) is True
        assert len(store.polished_blocks()) == 1


class TestWhenTheModelIsNotAvailable:
    async def test_an_unreachable_model_leaves_the_page_as_it_is(self, store) -> None:
        fill(store)
        dead = ScriptedBackend(error=LlmUnreachableError("nothing there"))
        worker, events, _ = build(store, dead)

        assert await worker.poll() is False
        assert store.polished_blocks() == []
        assert [name for name, _ in events] == ["error"]
        assert events[0][1]["code"] == "polish-unavailable"
        assert events[0][1]["transcription_continues"] is True

    async def test_a_dead_model_is_reported_once_rather_than_every_second(self, store) -> None:
        fill(store)
        dead = ScriptedBackend(error=LlmUnreachableError("nothing there"))
        worker, events, _ = build(store, dead)

        for _ in range(6):
            await worker.poll()

        assert len(events) == 1

    async def test_a_chunk_that_cannot_be_polished_is_eventually_abandoned(self, store) -> None:
        """Otherwise a dead server is sent an ever-larger request every second for two hours."""
        fill(store)
        dead = ScriptedBackend(error=LlmUnreachableError("nothing there"))
        worker, _, _ = build(store, dead)

        for _ in range(MAX_ATTEMPTS):
            assert await worker.poll() is False

        assert worker.cursor == store.last_segment_end()

    async def test_a_provider_that_cannot_be_built_is_not_a_crash(self, store) -> None:
        fill(store)
        config = default_config()

        def explode():
            raise RuntimeError("no model is configured")

        events: list[tuple[str, dict]] = []
        worker = PolishWorker(
            store=store,
            config_provider=lambda: config,
            backend_factory=explode,
            emit=lambda name, data: events.append((name, data)),
            silence_provider=lambda: 3.0,
        )

        assert await worker.poll() is False
        assert events[0][1]["code"] == "polish-unavailable"

    async def test_an_answer_that_is_all_reasoning_names_the_remedy(self, store) -> None:
        fill(store)
        worker, events, _ = build(store, ScriptedBackend(replies=[""], finish_reason="length"))

        assert await worker.poll() is False
        assert "output budget" in events[0][1]["message"]

    async def test_a_server_that_rejects_the_no_reasoning_fields_is_retried_without_them(
        self, store
    ) -> None:
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED], fail_times=1)
        worker, events, _ = build(store, backend)

        assert await worker.poll() is True
        assert backend.extras[0] != {}
        assert backend.extras[1] == {}
        assert events[0][0] == "transcript.polished"

    async def test_the_rejected_fields_are_not_sent_again(self, store) -> None:
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED, POLISHED], fail_times=1)
        worker, _, _ = build(store, backend)
        await worker.poll()

        store.append_segment(Segment(id=99, text=" ".join(RAW), start=200.0, end=280.0))
        await worker.poll()

        assert backend.extras[-1] == {}


class TestTheContentGuard:
    async def test_a_summary_is_discarded_and_the_raw_text_kept(self, store) -> None:
        fill(store)
        worker, events, _ = build(store, ScriptedBackend(replies=["He reported a measurement."]))

        assert await worker.poll() is False
        assert store.polished_blocks() == []
        assert events == []

    async def test_a_discarded_rewrite_is_not_retried(self, store) -> None:
        """A model that summarised when told to tidy will summarise again; the cost is the same."""
        fill(store)
        backend = ScriptedBackend(replies=["He reported a measurement.", POLISHED])
        worker, _, _ = build(store, backend)
        await worker.poll()

        assert worker.cursor == store.last_segment_end()
        assert len(backend.calls) == 1


class TestSettings:
    async def test_turning_it_off_mid_talk_takes_effect_on_the_next_tick(self, store) -> None:
        fill(store)
        backend = ScriptedBackend(replies=[POLISHED])
        worker, _, config = build(store, backend)

        config.polish.enabled = False
        assert await worker.poll() is False

        config.polish.enabled = True
        assert await worker.poll() is True

    async def test_a_shorter_interval_takes_effect_without_a_restart(self, store) -> None:
        fill(store, texts=RAW[:2])
        backend = ScriptedBackend(replies=[POLISHED_HALF])
        worker, _, config = build(store, backend)

        assert await worker.poll() is False

        config.polish.chunk_seconds = 20.0
        assert await worker.poll() is True


async def test_start_and_stop_flush_the_tail(store) -> None:
    """Stopping a session must not lose the minute that was still accumulating."""
    fill(store, texts=RAW[:2])
    backend = ScriptedBackend(replies=[POLISHED_HALF])
    worker, events, _ = build(store, backend, silence=0.0)

    worker.start()
    await worker.stop()

    assert len(store.polished_blocks()) == 1
    assert events[0][0] == "transcript.polished"


class TestTheInstructionsAreTheUsers:
    """The instruction list and each guard are settings, not laws (D-068).

    A guard exists because models comply unevenly with the shipped prompt. That reasoning stops
    holding the moment somebody writes their own: a rewritten rule asking for paragraphs produces
    none while `collapse_paragraphs` is on, and nothing on the page explains why. So each one is a
    switch — and turning one off never turns off the fallback, which is the raw transcript.
    """

    async def test_the_shipped_instructions_are_sent_when_nothing_was_written(self, store) -> None:
        fill(store)
        backend = ScriptedBackend([POLISHED])
        worker, _, _ = build(store, backend)

        await worker.poll()

        assert backend.systems[0] == DEFAULT_POLISH_PROMPT

    async def test_a_written_instruction_list_is_sent_instead(self, store) -> None:
        fill(store)
        backend = ScriptedBackend([POLISHED])
        worker, _, _ = build(store, backend, instructions="Keep every hesitation exactly as said.")

        await worker.poll()

        assert backend.systems[0] == "Keep every hesitation exactly as said."

    async def test_a_cleared_field_falls_back_rather_than_sending_nothing(self, store) -> None:
        """Selecting all and deleting leaves a newline behind, and a model given no instructions
        at all would be handed a page of transcript fragments with nothing asked of it."""
        fill(store)
        backend = ScriptedBackend([POLISHED])
        worker, _, _ = build(store, backend, instructions="   \n  ")

        await worker.poll()

        assert backend.systems[0] == DEFAULT_POLISH_PROMPT

    async def test_paragraphs_survive_when_collapsing_is_turned_off(self, store) -> None:
        fill(store)
        broken = POLISHED.replace("[00:32]", "\n\n[00:32]")
        worker, events, _ = build(store, ScriptedBackend([broken]), collapse_paragraphs=False)

        await worker.poll()

        assert "\n\n" in events[0][1]["text"]

    async def test_paragraphs_are_still_collapsed_by_default(self, store) -> None:
        fill(store)
        broken = POLISHED.replace("[00:32]", "\n\n[00:32]")
        worker, events, _ = build(store, ScriptedBackend([broken]))

        await worker.poll()

        assert "\n\n" not in events[0][1]["text"]

    async def test_an_invented_timestamp_survives_when_reconciliation_is_off(self, store) -> None:
        """Documented as a cost, not a feature: the marker points at a moment nobody chose."""
        fill(store)
        invented = POLISHED.replace("[00:48]", "[09:99]")
        worker, events, _ = build(store, ScriptedBackend([invented]), reconcile_timestamps=False)

        await worker.poll()

        assert "[09:99]" in events[0][1]["text"]

    async def test_an_invented_timestamp_is_removed_by_default(self, store) -> None:
        fill(store)
        invented = POLISHED.replace("[00:48]", "[09:99]")
        worker, events, _ = build(store, ScriptedBackend([invented]))

        await worker.poll()

        assert "[09:99]" not in events[0][1]["text"]

    async def test_markup_survives_when_stripping_is_turned_off(self, store) -> None:
        fill(store)
        decorated = POLISHED.replace("forty two", "**forty two**")
        worker, events, _ = build(store, ScriptedBackend([decorated]), strip_decoration=False)

        await worker.poll()

        assert "**forty two**" in events[0][1]["text"]

    async def test_a_longer_rewrite_is_kept_when_the_ceiling_is_raised(self, store) -> None:
        """An instruction list that expands spoken identifiers legitimately returns more words
        than the shipped one, and a fixed ceiling would discard every result it produced."""
        fill(store)
        long_answer = POLISHED + " " + " ".join(["expanded"] * 40)
        worker, events, _ = build(store, ScriptedBackend([long_answer]), max_expansion_ratio=5.0)

        await worker.poll()

        assert [name for name, _ in events] == ["transcript.polished"]

    async def test_that_same_rewrite_is_discarded_at_the_shipped_ceiling(self, store) -> None:
        fill(store)
        long_answer = POLISHED + " " + " ".join(["invented"] * 40)
        worker, events, _ = build(store, ScriptedBackend([long_answer]))

        await worker.poll()

        assert events == []
