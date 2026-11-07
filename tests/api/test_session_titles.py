"""Naming recordings over HTTP (D-084): the approval-gated backfill, and renaming.

The plan answers without starting anything; naming starts only when asked; a rename replaces any
title and reaches the listing.
"""

from __future__ import annotations

import time

import pytest
from app.models.segment import Segment
from app.services.transcript.store import TranscriptStore

from tests.api.test_session_media import KEY, client, dirs  # noqa: F401 - fixtures
from tests.api.test_transcribe_recording import write_empty_session
from tests.assistant.test_titles import ScriptedModel


def finished_talk(sessions, key: str = KEY, words: int = 60) -> None:
    path = write_empty_session(sessions, key)
    with TranscriptStore(path) as store:
        text = " ".join(f"gradient{n}" for n in range(words))
        store.append_segment(Segment(id=1, text=text, start=0.0, end=30.0))
        store.mark_ended()


def listed(client, key: str = KEY) -> dict:  # noqa: F811
    return next(row for row in client.get("/api/sessions").json()["sessions"] if row["key"] == key)


@pytest.fixture
def model(client):  # noqa: F811
    scripted = ScriptedModel()
    client.app.state.titles._backend_factory = lambda: scripted
    return scripted


def test_the_plan_counts_untitled_recordings_and_starts_nothing(client, dirs, model) -> None:  # noqa: F811
    finished_talk(dirs[0])
    model.replies = ["Gradient Descent Step Sizes"]

    plan = client.get("/api/sessions/untitled/plan").json()

    assert plan["count"] == 1 and plan["configured"] and plan["model"] == "scripted"
    assert plan["backfill"]["running"] is False
    assert model.asked == []
    assert listed(client)["named"] is False


def test_naming_without_a_model_is_refused(client, dirs) -> None:  # noqa: F811
    finished_talk(dirs[0])
    client.app.state.titles._backend_factory = lambda: ScriptedModel(model_id="")

    response = client.post("/api/sessions/untitled/name")

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "no-model"


def test_the_backfill_names_an_untitled_recording(client, dirs, model) -> None:  # noqa: F811
    finished_talk(dirs[0])
    model.replies = ["Gradient Descent Step Sizes"]

    started = client.post("/api/sessions/untitled/name").json()
    assert started["total"] == 1

    deadline = time.monotonic() + 5
    while client.get("/api/sessions/untitled/plan").json()["backfill"]["running"]:
        assert time.monotonic() < deadline, "the backfill did not finish"
        time.sleep(0.02)

    row = listed(client)
    assert row["title"] == "Gradient Descent Step Sizes" and row["named"] is True
    assert client.get("/api/sessions/untitled/plan").json()["count"] == 0


def test_a_rename_round_trips_and_replaces_any_title(client, dirs) -> None:  # noqa: F811
    finished_talk(dirs[0])

    for title in ("First  name ", "Second name"):
        response = client.patch(f"/api/sessions/{KEY}", json={"title": title})
        assert response.status_code == 200

    assert response.json() == {"key": KEY, "title": "Second name"}
    assert listed(client)["title"] == "Second name"


def test_a_rename_needs_words_and_a_session(client, dirs) -> None:  # noqa: F811
    finished_talk(dirs[0])

    assert client.patch(f"/api/sessions/{KEY}", json={"title": "   "}).status_code == 422
    assert client.patch("/api/sessions/20990101-000000-x", json={"title": "A"}).status_code == 404
    assert client.patch(f"/api/sessions/{KEY}", json={"title": "x" * 121}).status_code == 422
