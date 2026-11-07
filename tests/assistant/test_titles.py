"""Titles the language model writes for a finished recording (D-084).

A scripted client stands in for the model, so what is tested is everything around it: what the
model is shown, how its answer is cleaned, that junk is stored as nothing, and that a title already
there — one the user chose — is never replaced.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from app.models.segment import Segment
from app.models.session import SessionMetadata
from app.services.llm.errors import LlmError
from app.services.transcript import titles
from app.services.transcript.glance import Glance
from app.services.transcript.store import TranscriptStore
from app.services.transcript.titles import TitleService, clean_title, keyword_title, source_text

KEY = "20261107-120000-a1a1a1a1a1a1"


class ScriptedModel:
    """Answers each request with the next reply, or raises."""

    def __init__(self, *replies: str, error: Exception | None = None, model_id: str = "scripted"):
        self.replies = list(replies)
        self.error = error
        self.model_id = model_id
        self.asked: list[str] = []

    async def complete(self, messages, options=None):  # noqa: ANN001, ANN201
        if self.error:
            raise self.error
        self.asked.append(messages[-1].content)
        return self.replies.pop(0) if self.replies else ""


def talk(path, *, words: int = 60, title: str = "", summaries: tuple[str, ...] = ()) -> None:
    metadata = SessionMetadata(session_id=KEY.rsplit("-", 1)[1], title=title)
    with TranscriptStore(path, metadata=metadata) as store:
        text = " ".join(f"gradient{n}" for n in range(words))
        store.append_segment(Segment(id=1, text=text, start=0.0, end=30.0))
        for index, summary in enumerate(summaries):
            store.add_summary(index * 60.0, index * 60.0 + 60.0, summary)


@pytest.fixture
def setup(tmp_path):
    """A session directory with one talk, and a service reading it."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    config = SimpleNamespace(
        storage=SimpleNamespace(session_dir=str(sessions)),
        recording=SimpleNamespace(recording_dir=str(tmp_path / "recordings")),
        context=SimpleNamespace(summary_enabled=True),
    )
    events: list[tuple[str, dict]] = []
    model = ScriptedModel()

    def build(model_for_service=None):  # noqa: ANN001, ANN202
        chosen = model_for_service or model
        return TitleService(
            config_provider=lambda: config,
            backend_factory=lambda: chosen,
            emit=lambda event, data: events.append((event, data)),
        )

    return SimpleNamespace(path=sessions / f"{KEY}.db", model=model, events=events, build=build)


def stored_title(path) -> str:
    with TranscriptStore(path) as store:
        return store.metadata().title


# -- cleaning the answer -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Gradient Descent and Learning Rates", "Gradient Descent and Learning Rates"),
        ('"Gradient Descent and Learning Rates."', "Gradient Descent and Learning Rates"),
        ("Title: Gradient Descent Basics", "Gradient Descent Basics"),
        ("**Gradient Descent Basics**\n\nThis talk covers…", "Gradient Descent Basics"),
        ("  \n Convex   Optimisation  Review ", "Convex Optimisation Review"),
    ],
)
def test_an_answer_is_cleaned_into_a_title(raw, expected) -> None:
    assert clean_title(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "Gradients",
        "I'm sorry, I can't name this recording without more context.",
        "Here is a title for the recording",
        "one two three four five six seven eight nine ten eleven",
        "--- ...",
    ],
)
def test_an_answer_that_is_not_a_title_is_nothing(raw) -> None:
    assert clean_title(raw) == ""


def test_a_long_title_is_cut_at_a_word_and_kept_under_sixty_characters() -> None:
    title = clean_title(
        "Extraordinarily Comprehensive Introduction To Stochastic Approximation Theory"
    )

    assert 0 < len(title) <= titles.MAX_CHARS
    assert not title.endswith(" ")
    assert "Stochastic" in title


def test_the_fallback_is_the_keywords_and_is_never_stored() -> None:
    assert keyword_title(Glance(keywords=["Gradient", "Descent", "Rate", "Step"])) == (
        "Gradient · Descent · Rate"
    )


# -- what the model is shown ---------------------------------------------------------------------


def test_too_little_said_is_not_named(setup) -> None:
    talk(setup.path, words=titles.MIN_WORDS - 1)

    with TranscriptStore(setup.path) as store:
        assert source_text(store) == ""


def test_summaries_are_preferred_to_the_transcript(setup) -> None:
    talk(setup.path, summaries=("The speaker introduces gradient descent.",))

    with TranscriptStore(setup.path) as store:
        assert source_text(store) == "The speaker introduces gradient descent."


def test_without_summaries_the_opening_is_read(setup) -> None:
    talk(setup.path, words=900)

    with TranscriptStore(setup.path) as store:
        text = source_text(store)

    assert text.startswith("gradient0 ") and len(text.split()) == titles.SOURCE_WORDS


# -- writing it ----------------------------------------------------------------------------------


def test_a_finished_talk_is_named_and_the_page_is_told(setup) -> None:
    talk(setup.path)
    setup.model.replies = ["Gradient Descent Step Sizes."]

    title = asyncio.run(setup.build().name(KEY))

    assert title == "Gradient Descent Step Sizes"
    assert stored_title(setup.path) == title
    assert setup.events == [("session.titled", {"key": KEY, "title": title})]


def test_an_existing_title_is_never_replaced_and_the_model_is_not_asked(setup) -> None:
    talk(setup.path, title="My own name for it")
    setup.model.replies = ["Gradient Descent Step Sizes"]

    assert asyncio.run(setup.build().name(KEY)) == ""
    assert stored_title(setup.path) == "My own name for it"
    assert setup.model.asked == []


@pytest.mark.parametrize("reply", ["", "Sorry, I cannot help with that request today."])
def test_an_empty_or_junk_answer_is_stored_as_nothing(setup, reply) -> None:
    talk(setup.path)
    setup.model.replies = [reply]

    assert asyncio.run(setup.build().name(KEY)) == ""
    assert stored_title(setup.path) == ""
    assert setup.events == []


def test_a_model_that_fails_leaves_the_recording_untitled(setup) -> None:
    talk(setup.path)
    failing = ScriptedModel(error=LlmError("The server is not running."))

    assert asyncio.run(setup.build(failing).name(KEY)) == ""
    assert stored_title(setup.path) == ""


def test_with_no_model_chosen_nothing_is_asked(setup) -> None:
    talk(setup.path)
    unset = ScriptedModel("Gradient Descent Step Sizes", model_id="")

    assert asyncio.run(setup.build(unset).name(KEY)) == ""
    assert unset.asked == []


def test_a_title_the_user_typed_meanwhile_wins_over_the_model(setup) -> None:
    """The store refuses to replace a title when asked not to, whatever the order of events."""
    talk(setup.path)
    with TranscriptStore(setup.path) as store:
        assert store.set_title("Typed by hand")
        assert not store.set_title("From the model", replace=False)
        assert store.metadata().title == "Typed by hand"


def test_background_naming_waits_for_summaries_to_be_allowed(setup) -> None:
    talk(setup.path)
    service = setup.build()
    service._config().context.summary_enabled = False

    async def run() -> None:
        service.bind_loop(asyncio.get_running_loop())
        service.request(KEY)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert setup.model.asked == []


def test_a_request_from_another_thread_names_the_talk_in_the_background(setup) -> None:
    talk(setup.path)
    setup.model.replies = ["Gradient Descent Step Sizes"]
    service = setup.build()

    async def run() -> None:
        service.bind_loop(asyncio.get_running_loop())
        await asyncio.to_thread(service.request, KEY)
        for _ in range(100):
            if setup.events:
                return
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert stored_title(setup.path) == "Gradient Descent Step Sizes"
