"""The assistant's composer (redesign Step 8): suggested questions in a pop-up, an icon-only Ask.

The quick questions were a row of chips over the box; they are a menu behind an icon now. Both
icons need names, the menu must be a menu of `menuitem`s, and the button must say it opens one.
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

    store = ConfigStore(config_path=tmp_path_factory.mktemp("composer") / "config.json")
    with TestClient(create_app(config=store)) as client:
        return parse(client.get("/").text)


def _one(page: list[Element], hook: str) -> Element:
    [element] = [e for e in page if hook in e.attrs]
    return element


def test_the_menu_button_is_named_and_says_it_opens_a_menu(page) -> None:
    button = _one(page, "data-quick-toggle")
    assert button.tag == "button" and button.named
    assert button.attrs.get("aria-haspopup") == "menu"
    assert button.attrs.get("aria-expanded") == "false"
    menu = _one(page, "data-quick-actions")
    assert button.attrs.get("aria-controls") == menu.attrs.get("id")


def test_the_menu_is_a_hidden_labelled_menu(page) -> None:
    menu = _one(page, "data-quick-actions")
    assert menu.attrs.get("role") == "menu"
    assert menu.attrs.get("aria-label") == "Suggested questions"
    assert "hidden" in menu.attrs


def test_ask_is_an_icon_with_a_name(page) -> None:
    send = _one(page, "data-chat-send")
    assert send.attrs.get("aria-label") == "Ask" and send.attrs.get("type") == "submit"
    assert not send.text.strip(), "Ask should be an icon, not a word"


def test_the_menu_items_are_menuitems_and_the_chips_are_gone() -> None:
    pane = (FRONTEND / "static/js/components/chat-pane.js").read_text(encoding="utf-8")
    assert 'role: "menuitem"' in pane
    assert '"quick-action"' not in pane
    chat_css = (FRONTEND / "static/css/components/chat.css").read_text(encoding="utf-8")
    assert ".quick-action " not in chat_css and ".quick-action {" not in chat_css


def test_the_question_bubble_is_neutral() -> None:
    chat_css = (FRONTEND / "static/css/components/chat.css").read_text(encoding="utf-8")
    rule = chat_css.split(".message--user .message__text {", 1)[1].split("}", 1)[0]
    assert "var(--surface-control)" in rule and "accent" not in rule
