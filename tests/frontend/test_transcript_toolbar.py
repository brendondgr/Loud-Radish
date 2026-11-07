"""The transcript pane's toolbar and the text size (redesign Step 6).

The toolbar is icons with names; the two text-size buttons are gone and the size is a slider in
Settings → Context; the glossary button carries its count and, with the assistant's, drives the one
panel — the toolbar's had a hook nothing read, so it did nothing.
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

    store = ConfigStore(config_path=tmp_path_factory.mktemp("toolbar") / "config.json")
    with TestClient(create_app(config=store)) as client:
        return parse(client.get("/").text)


def test_the_text_size_buttons_are_gone_and_settings_has_the_size(page) -> None:
    assert not [e for e in page if "data-text-size" in e.attrs]
    [slider] = [e for e in page if "data-text-size-control" in e.attrs]
    assert slider.attrs.get("type") == "range"
    assert (slider.attrs.get("min"), slider.attrs.get("max")) == ("14", "28")
    [label] = [e for e in page if e.tag == "label" and e.attrs.get("for") == slider.attrs["id"]]
    assert "text size" in label.text.lower()


def test_every_glossary_button_drives_the_panel_and_shows_a_count(page) -> None:
    toggles = [e for e in page if "data-glossary-toggle" in e.attrs]
    badges = [e for e in page if "data-glossary-count" in e.attrs]
    assert len(toggles) == 2 and len(badges) == 2
    assert all(e.attrs.get("aria-controls") == "glossary-panel" for e in toggles)
    assert not [e for e in page if "data-toggle-glossary" in e.attrs], "the dead hook is back"
    main = (FRONTEND / "static/js/main.js").read_text(encoding="utf-8")
    assert '$$("[data-glossary-toggle]")' in main and '$$("[data-glossary-count]")' in main


def test_the_toolbars_icon_buttons_have_names(page) -> None:
    for hook in ("data-toggle-raw", "data-open-recording"):
        named = [e for e in page if hook in e.attrs and e.named]
        assert named, hook


def test_the_default_size_is_the_owners_smaller_one() -> None:
    tokens = (FRONTEND / "static/css/tokens.css").read_text(encoding="utf-8")
    storage = (FRONTEND / "static/js/core/storage.js").read_text(encoding="utf-8")
    assert "--transcript-size: 16.5px;" in tokens
    assert "transcriptSize: 16.5," in storage
