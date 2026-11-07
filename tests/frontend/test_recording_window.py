"""The recording window replaces the recordings row's export controls (D-077).

Reported: a row could carry an "Export" link (the transcript) and an "Export…" button (the web
app), two controls with the same name that did different things. These pin the shape that fixed
it — one window, rendered on both pages, whose footer names its buttons apart — and that nothing
still loads the component it replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import ConfigStore
from fastapi.testclient import TestClient

from tests.frontend.test_rendered_accessibility import Element, parse

FRONTEND = Path(__file__).resolve().parents[2] / "web" / "frontend"


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, list[Element]]:
    from app.main import create_app

    store = ConfigStore(config_path=tmp_path_factory.mktemp("window") / "config.json")
    with TestClient(create_app(config=store)) as client:
        return {path: parse(client.get(path).text) for path in ("/", "/sessions")}


def _window(elements: list[Element]) -> list[Element]:
    """The elements from the recording window's root to the next dialog's, or the page's end."""
    start = next(i for i, e in enumerate(elements) if "data-recording-window" in e.attrs)
    end = next(
        (
            i
            for i, e in enumerate(elements[start + 1 :], start + 1)
            if e.tag == "div" and "modal" in e.attrs.get("class", "").split()
        ),
        len(elements),
    )
    return elements[start:end]


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_both_pages_render_the_one_recording_window(pages, path) -> None:
    elements = pages[path]
    roots = [e for e in elements if "data-recording-window" in e.attrs]
    assert len(roots) == 1
    assert not [e for e in elements if "data-export" in e.attrs], "the old window root remains"

    window = _window(elements)
    dialog = next(e for e in window if e.attrs.get("role") == "dialog")
    assert dialog.attrs.get("aria-modal") == "true"
    assert dialog.attrs.get("aria-labelledby")
    assert any("data-window-overview" in e.attrs for e in window)


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_no_two_footer_actions_share_a_name(pages, path) -> None:
    window = _window(pages[path])
    names = [
        (e.attrs.get("aria-label") or e.text).strip()
        for e in window
        if e.tag in ("button", "a") and "modal__close" not in e.attrs.get("class", "")
    ]
    names = [name for name in names if name]
    assert len(names) == len(set(names)), names


def test_nothing_still_loads_the_replaced_component() -> None:
    assert not (FRONTEND / "static/js/components/export-dialog.js").exists()
    assert not (FRONTEND / "templates/partials/export.html").exists()
    for script in (FRONTEND / "static/js").rglob("*.js"):
        text = script.read_text(encoding="utf-8")
        assert "export-dialog.js" not in text, script
        assert "ExportDialog" not in text, script


def test_the_recordings_row_no_longer_carries_exports() -> None:
    """The row's only controls are Open… and Delete; every export is a window section."""
    page = (FRONTEND / "static/js/sessions.js").read_text(encoding="utf-8")
    assert "sessionExportUrl" not in page
    assert "sessionChatUrl" not in page
    overview = (FRONTEND / "static/js/components/recording-overview.js").read_text(encoding="utf-8")
    assert "sessionExportUrl" in overview
    assert "sessionChatUrl" in overview
