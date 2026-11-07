"""The main screen's pieces for a past recording (D-076), as rendered.

The banner names the recording and can be closed; the picker and the approval dialog are real
dialogs with headings; the approval dialog's one choice has a label; and the "Open a recording…"
control is reachable whether or not a transcript is already on screen.
"""

from __future__ import annotations

import pytest
from app.config import ConfigStore
from fastapi.testclient import TestClient

from tests.frontend.test_rendered_accessibility import Element, parse


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, list[Element]]:
    from app.main import create_app

    store = ConfigStore(config_path=tmp_path_factory.mktemp("viewing") / "config.json")
    with TestClient(create_app(config=store)) as client:
        return {path: parse(client.get(path).text) for path in ("/", "/sessions")}


def _with(elements: list[Element], attribute: str) -> list[Element]:
    return [e for e in elements if attribute in e.attrs]


def test_the_banner_is_labelled_starts_hidden_and_can_be_closed(pages) -> None:
    [banner] = _with(pages["/"], "data-viewing-banner")
    assert "hidden" in banner.attrs
    assert banner.attrs.get("aria-labelledby") == "viewing-title"
    [close] = _with(pages["/"], "data-viewing-close")
    assert close.tag == "button" and close.text.strip() == "Close"


def test_a_recording_can_be_opened_from_the_toolbar_and_the_empty_state(pages) -> None:
    openers = _with(pages["/"], "data-open-recording")
    # The toolbar, the empty state, and the banner's "Open another…".
    assert len(openers) == 3
    assert all(e.tag == "button" and e.named for e in openers)


@pytest.mark.parametrize(
    ("path", "root"),
    [
        ("/", "data-recording-picker"),
        ("/", "data-transcribe-consent"),
        ("/sessions", "data-transcribe-consent"),
    ],
)
def test_each_dialog_is_a_labelled_modal(pages, path, root) -> None:
    elements = pages[path]
    start = next(i for i, e in enumerate(elements) if root in e.attrs)
    dialog = next(e for e in elements[start:] if e.attrs.get("role") == "dialog")
    assert dialog.attrs.get("aria-modal") == "true"
    heading = dialog.attrs["aria-labelledby"]
    assert any(e.attrs.get("id") == heading and e.text.strip() for e in elements)


def test_the_approval_dialogs_one_choice_is_labelled_and_on_by_default(pages) -> None:
    [checkbox] = _with(pages["/"], "data-consent-open")
    assert checkbox.label is not None
    assert "main screen" in checkbox.label.text
    assert "checked" in checkbox.attrs


def test_the_banner_is_a_card_at_the_top_of_the_transcript_pane(pages) -> None:
    """Redesign Step 6: inside the pane it names, above the scroller (it was under the header)."""
    elements = pages["/"]
    pane = next(i for i, e in enumerate(elements) if "data-transcript-pane" in e.attrs)
    banner = next(i for i, e in enumerate(elements) if "data-viewing-banner" in e.attrs)
    scroller = next(i for i, e in enumerate(elements) if "data-transcript-scroller" in e.attrs)
    assert pane < banner < scroller
