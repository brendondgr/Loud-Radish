"""The assistant's model can be typed, and the address has a Get button (D-067).

The model field used to be a dropdown holding only what the last connection test returned, so a
model the server did not list — or a name the user simply knew — could not be entered at all. And
beside the address sat a dropdown of "common servers" that rewrote the address, which nobody had
asked for and which occupied exactly the spot where a way to list models was wanted.

Read as rendered HTML and as source rather than run: decision D-011 removed the Node toolchain on
purpose, so there is no frontend test runner. `tests/utils/test_mode_vocabulary.py` establishes the
precedent, and the same caveat applies — these pin the shape of the form, not that it executes. The
behaviour itself is verified in the browser.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.paths import STATIC_DIR
from fastapi.testclient import TestClient

LLM_JS = STATIC_DIR / "js" / "components" / "settings" / "llm.js"


class _Elements(HTMLParser):
    """Every start tag with its attributes, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))


@pytest.fixture
def elements(tmp_path):
    store = ConfigStore(config_path=tmp_path / "config.json")
    with TestClient(create_app(config=store)) as client:
        page = client.get("/").text
    parser = _Elements()
    parser.feed(page)
    return parser.elements


def _with(elements, attribute: str):
    return [(tag, attrs) for tag, attrs in elements if attribute in attrs]


def test_the_model_is_a_text_field_that_is_also_a_dropdown(elements) -> None:
    """The reported fault: a `<select>` could not take a name the server had not listed, and a
    plain text field gave nothing to pick from. A combobox is both."""
    fields = _with(elements, "data-llm-model-input")
    assert [tag for tag, _ in fields] == ["input", "input"], "one typeable field per mode"
    for _, attrs in fields:
        assert attrs.get("type") == "text"
        assert attrs.get("role") == "combobox"
        assert attrs.get("aria-controls"), "the field must name the list it opens"

    lists = {attrs.get("id") for tag, attrs in elements if attrs.get("role") == "listbox"}
    assert {attrs["aria-controls"] for _, attrs in fields} <= lists, "every field's list exists"
    assert not _with(elements, "data-llm-model-select"), "the select is gone"
    assert not [tag for tag, _ in elements if tag == "datalist"], "and so is the datalist"


def test_the_address_has_a_get_button_and_no_server_dropdown(elements) -> None:
    buttons = _with(elements, "data-llm-get")
    assert [tag for tag, _ in buttons] == ["button", "button"], "one per mode panel"
    assert not _with(elements, "data-llm-preset"), "the common-servers dropdown is gone"


def test_get_lists_models_from_the_address_on_screen_without_judging_the_model() -> None:
    """Get reads the field, not the store, and asks the listing route rather than the connection
    test — which refuses when the *saved* model is not at the new address, the exact pop-up that
    was reported."""
    source = LLM_JS.read_text(encoding="utf-8")

    assert "getModels()" in source
    assert "api.llmModelsAt(this._overrides())" in source
    assert "api.testLlm(this._overrides())" in source, "the test still tests"
    assert "applyPreset" not in source, "nothing rewrites the address any more"


def test_what_is_typed_is_what_is_saved() -> None:
    """The field's value goes to the store on change, whether or not the list held it."""
    source = LLM_JS.read_text(encoding="utf-8")

    assert "input.value.trim()" in source
    assert "config.patch({ [path]: value })" in source
