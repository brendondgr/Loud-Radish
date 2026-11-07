"""The details column (D-086): the video, this recording, capture, and the glossary.

It replaced the recording monitor, which existed only in window mode, and the glossary panel that
opened over the transcript. These pin its shape: a labelled column before the transcript, the
monitor's hooks inside it (so `recording-monitor.js` drives it unchanged), the glossary inside it,
and a Details tab in place of the Monitor one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import ConfigStore
from fastapi.testclient import TestClient

from tests.frontend.test_rendered_accessibility import Element, parse

FRONTEND = Path(__file__).resolve().parents[2] / "web" / "frontend"


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> list[Element]:
    from app.main import create_app

    store = ConfigStore(config_path=tmp_path_factory.mktemp("details") / "config.json")
    with TestClient(create_app(config=store)) as client:
        return parse(client.get("/").text)


def _index(elements: list[Element], attribute: str) -> int:
    return next(i for i, e in enumerate(elements) if attribute in e.attrs)


def _column(elements: list[Element]) -> list[Element]:
    start = _index(elements, "data-details-pane")
    return elements[start : _index(elements, "data-transcript-pane")]


def test_the_column_is_a_labelled_aside_before_the_transcript(page) -> None:
    [pane] = [e for e in page if "data-details-pane" in e.attrs]
    assert pane.tag == "aside" and "hidden" not in pane.attrs
    heading = pane.attrs["aria-labelledby"]
    assert any(e.attrs.get("id") == heading and e.text.strip() == "Details" for e in page)
    assert _index(page, "data-details-pane") < _index(page, "data-transcript-pane")
    assert _index(page, "data-transcript-pane") < _index(page, "data-chat-pane")


def test_each_section_is_named_by_its_heading(page) -> None:
    sections = [e for e in _column(page) if e.tag == "section"]
    assert len(sections) == 4
    ids = {e.attrs.get("id"): e.text.strip() for e in page if e.attrs.get("id")}
    names = [ids[section.attrs["aria-labelledby"]] for section in sections]
    assert names == ["Video", "This recording", "Capture", "Glossary"]


@pytest.mark.parametrize(
    "hook",
    [
        "data-monitor-pane",
        "data-monitor-preview",
        "data-monitor-empty",
        "data-monitor-stats",
        "data-monitor-clock",
        "data-details-started",
        "data-details-length",
        "data-details-words",
        "data-details-questions",
        "data-details-source",
        "data-details-timing",
        "data-details-model",
        "data-glossary-panel",
        "data-glossary-list",
    ],
)
def test_every_part_is_in_the_column(page, hook) -> None:
    column = page[_index(page, "data-details-pane") : _index(page, "data-transcript-pane")]
    assert [e for e in column if hook in e.attrs], hook


def test_the_capture_gear_is_named(page) -> None:
    [gear] = [e for e in page if "data-details-settings" in e.attrs]
    assert gear.tag == "button" and gear.named


def test_the_tab_bar_has_three_tabs_and_no_monitor(page) -> None:
    tabs = [e for e in page if "data-pane-tab" in e.attrs]
    assert [t.attrs["data-pane-tab"] for t in tabs] == ["transcript", "chat", "details"]
    assert [t.attrs.get("aria-selected") for t in tabs] == ["true", "false", "false"]
    assert not [t for t in tabs if "hidden" in t.attrs]


def test_the_monitor_pane_and_the_glossary_overlay_are_gone() -> None:
    assert not (FRONTEND / "templates/partials/monitor").exists()
    assert not (FRONTEND / "templates/partials/transcript/glossary_panel.html").exists()
    main = (FRONTEND / "static/js/main.js").read_text(encoding="utf-8")
    assert "renderMonitorVisibility" not in main
    assert "glossaryOpen" not in (FRONTEND / "static/js/core/storage.js").read_text(
        encoding="utf-8"
    )
