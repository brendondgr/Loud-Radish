"""The Recordings page as wide cards (D-085), as served.

There is no build step and no browser in the suite, so a misspelt import or a stylesheet that is
not served is found by a person and nobody else. These check the page's modules resolve, its
stylesheets are served, it has a place to draw each part, and that the controls the cards and the
compact window draw from JavaScript carry names — which a browser pass then confirms.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.config import ConfigStore
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[2] / "web" / "frontend" / "static"
JS = STATIC / "js"
RECORDINGS = JS / "recordings"
IMPORT = re.compile(r"""^\s*import\s[^;]*?from\s+["'](\.[^"']+)["']""", re.MULTILINE | re.DOTALL)


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> str:
    from app.main import create_app

    store = ConfigStore(config_path=tmp_path_factory.mktemp("recordings") / "config.json")
    with TestClient(create_app(config=store)) as client:
        html = client.get("/sessions").text
        for sheet in re.findall(r'href="(/static/css/[^"]+)"', html):
            assert client.get(sheet).status_code == 200, sheet
    return html


MODULES = [JS / "sessions.js", *sorted(RECORDINGS.glob("*.js"))]


@pytest.mark.parametrize("module", MODULES, ids=lambda path: path.name)
def test_every_import_resolves(module: Path) -> None:
    for target in IMPORT.findall(module.read_text(encoding="utf-8")):
        assert (module.parent / target).resolve().is_file(), f"{module.name} imports {target}"


def test_the_page_loads_its_own_styles_and_nothing_of_the_old_rows(page) -> None:
    assert "/static/css/components/recordings.css" in page
    assert "/static/css/components/recording-detail.css" in page
    assert "components/sessions.css" not in page
    assert not (STATIC / "css" / "components" / "sessions.css").exists()


def test_the_page_has_somewhere_to_draw_each_part(page) -> None:
    for hook in (
        "data-recordings",
        "data-recordings-toolbar",
        "data-recordings-list",
        "data-recording-detail",
        "data-recordings-status",
        "data-recording-window",
        "data-transcribe-consent",
    ):
        assert hook in page, hook
    # The list is a landmark with a name, and the status line is announced.
    assert re.search(r'<main[^>]*aria-label="Recordings"', page)
    assert re.search(r'data-recordings-status[^>]*role="status"', page) or re.search(
        r'role="status"[^>]*data-recordings-status', page
    )


def test_the_toolbar_has_a_labelled_search_and_a_filter_radiogroup() -> None:
    source = (RECORDINGS / "toolbar.js").read_text(encoding="utf-8")
    assert '"aria-label": "Search recordings"' in source
    assert 'role: "radiogroup", "aria-label": "Show"' in source
    assert 'role: "radio"' in source and '"aria-checked"' in source
    assert '"aria-label": "Sort"' in source


def test_every_card_action_has_an_accessible_name() -> None:
    """An icon-only button is named by its action's label; the ⋯ button and Close by their own."""
    card = (RECORDINGS / "card.js").read_text(encoding="utf-8")
    assert '"aria-label": labelled ? null : action.label' in card
    menu = (RECORDINGS / "menu.js").read_text(encoding="utf-8")
    assert '"aria-label": "More actions"' in menu and 'role: "menuitem"' in menu
    detail = (RECORDINGS / "detail.js").read_text(encoding="utf-8")
    assert '"aria-label": "Close"' in detail and '"aria-label": "Rename this recording"' in detail

    actions = (RECORDINGS / "actions.js").read_text(encoding="utf-8")
    pushed = re.findall(r"list\.push\(\{(.*?)\}\);", actions, re.S)
    assert pushed and all("label:" in action for action in pushed)


def test_the_compact_window_is_a_labelled_modal_that_carries_every_export() -> None:
    detail = (RECORDINGS / "detail.js").read_text(encoding="utf-8")
    dialog = 'role: "dialog", "aria-modal": "true", "aria-labelledby": "recording-detail-title"'
    assert dialog in detail
    assert 'id: "recording-detail-title"' in detail
    # The transcript in every format and the version choice (D-066); the conversation; the web app.
    assert "TRANSCRIPT_FORMATS" in detail and '"Transcript version"' in detail
    actions = (RECORDINGS / "actions.js").read_text(encoding="utf-8")
    assert "sessionChatUrl" in actions and 'run: "webapp"' in actions
    assert "sessionExportUrl" in actions


def test_dates_come_from_started_at_not_the_title() -> None:
    describe = (RECORDINGS / "describe.js").read_text(encoding="utf-8")
    assert "new Date(session.started_at)" in describe
    # The lab recognised made-up titles by their wording; the server now says so (`named`).
    assert "DEFAULT_TITLE" not in describe and "session.named" in describe
