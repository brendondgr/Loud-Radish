"""The recording's health, as chips in the header (redesign Step 5).

There used to be a status bar along the bottom of the window. Its indicators moved into the header,
keeping every hook `components/status-bar.js` reads, so the event handling is untouched and only
the placement changed. These pin that: every hook is inside the header, nothing of the bar is left,
and the record button's dot sits in an icon box.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import ConfigStore
from fastapi.testclient import TestClient

from tests.frontend.test_rendered_accessibility import Element, parse

FRONTEND = Path(__file__).resolve().parents[2] / "web" / "frontend"

HOOKS = (
    "data-level-meter",
    "data-vad-dot",
    "data-vad-label",
    "data-rtf",
    "data-rtf-icon",
    "data-latency",
    "data-suppressed",
    "data-suppressed-item",
    "data-source-label",
    "data-connection-dot",
    "data-connection-label",
)


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> list[Element]:
    from app.main import create_app

    store = ConfigStore(config_path=tmp_path_factory.mktemp("header") / "config.json")
    with TestClient(create_app(config=store)) as client:
        return parse(client.get("/").text)


def _header(elements: list[Element]) -> list[Element]:
    start = next(
        i
        for i, e in enumerate(elements)
        if e.tag == "header" and "header" in e.attrs.get("class", "").split()
    )
    end = next(
        (
            i
            for i, e in enumerate(elements[start + 1 :], start + 1)
            if "data-banners" in e.attrs or e.tag == "nav"
        ),
        len(elements),
    )
    return elements[start:end]


@pytest.mark.parametrize("hook", HOOKS)
def test_every_status_bar_hook_is_in_the_header(page, hook) -> None:
    header = _header(page)
    assert [e for e in header if hook in e.attrs], f"{hook} is not in the header"
    assert len([e for e in page if hook in e.attrs]) == 1, f"{hook} appears more than once"


def test_the_health_chips_are_one_labelled_group(page) -> None:
    [group] = [e for e in page if "data-status-bar" in e.attrs]
    assert group.attrs.get("role") == "group"
    assert group.attrs.get("aria-label") == "Recording health"
    assert group in _header(page)


def test_nothing_of_the_status_bar_is_left() -> None:
    assert not (FRONTEND / "templates/partials/status_bar.html").exists()
    assert not (FRONTEND / "static/css/components/status-bar.css").exists()
    for template in (FRONTEND / "templates").rglob("*.html"):
        assert "status_bar.html" not in template.read_text(encoding="utf-8"), template
    assert "--status-height" not in (FRONTEND / "static/css/tokens.css").read_text(encoding="utf-8")


def test_the_record_buttons_dot_sits_in_an_icon_box(page) -> None:
    [button] = [e for e in page if "data-session-toggle" in e.attrs]
    assert "button--record" in button.attrs.get("class", "")
    [box] = [e for e in page if "data-session-toggle-icon" in e.attrs]
    assert "icon" in box.attrs.get("class", "").split()
    [label] = [e for e in page if "data-session-toggle-label" in e.attrs]
    assert label.text.strip() == "Start recording"
