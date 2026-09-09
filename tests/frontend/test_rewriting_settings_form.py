"""The Rewriting tab, and the empty-means-shipped rule it is built on (D-068).

Both passes that hand transcribed speech to a language model were driven by an instruction list
compiled into the source. Those instructions are judgements about whose talk it is — how much
filler to remove, whether a passage stays one paragraph — and they are now the user's to make.

Read as rendered HTML and as source rather than run, on the precedent of
`test_llm_settings_form.py`: D-011 removed the Node toolchain on purpose, so there is no frontend
test runner. These pin the shape of the panel and the rule its module is built on, not that it
executes. The behaviour itself was verified in a browser.

**The rule worth guarding is the one about the placeholder.** An empty box means the instructions
that shipped, and the shipped text therefore appears as a placeholder rather than as the field's
value. A field seeded with that text would become a stored copy the moment anybody touched it,
freezing this installation on today's wording — which is exactly what the empty state exists to
prevent, and exactly the shortcut a later edit is likely to take.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from app.config import ConfigStore
from app.main import create_app
from app.paths import STATIC_DIR
from fastapi.testclient import TestClient

REWRITING_JS = STATIC_DIR / "js" / "components" / "settings" / "rewriting.js"
BINDINGS_JS = STATIC_DIR / "js" / "components" / "settings" / "bindings.js"


class _Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


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


def test_the_tab_and_its_panel_point_at_each_other(elements) -> None:
    """A tab whose `aria-controls` names nothing is a tab that announces a panel and opens none."""
    tabs = [attrs for tag, attrs in elements if attrs.get("data-settings-tab") == "rewriting"]
    panels = [attrs for tag, attrs in elements if attrs.get("data-settings-panel") == "rewriting"]

    assert len(tabs) == 1 and len(panels) == 1
    assert tabs[0]["aria-controls"] == panels[0]["id"]
    assert panels[0]["aria-labelledby"] == tabs[0]["id"]
    assert panels[0].get("role") == "tabpanel"


def test_both_instruction_lists_are_editable(elements) -> None:
    fields = _with(elements, "data-instructions")
    assert [tag for tag, _ in fields] == ["textarea", "textarea"]
    assert {attrs["data-instructions"] for _, attrs in fields} == {
        "polish.instructions",
        "dictation.instructions",
    }
    for _, attrs in fields:
        assert attrs["data-config"] == attrs["data-instructions"], "bound to the path it names"
        assert attrs.get("aria-describedby"), "the state line has to be announced with the field"


def test_each_editor_has_a_reset_and_a_state_line(elements) -> None:
    """A box that is empty and a box that was just reset are the same picture without one."""
    resets = {
        attrs["data-instructions-reset"] for _, attrs in _with(elements, "data-instructions-reset")
    }
    assert resets == {"polish.instructions", "dictation.instructions"}

    described = {attrs["aria-describedby"] for _, attrs in _with(elements, "data-instructions")}
    states = {attrs.get("id") for _, attrs in _with(elements, "data-instructions-state")}
    assert described <= states, "every field's state line exists"


def test_the_shipped_text_is_a_placeholder_and_never_the_value() -> None:
    """The rule the whole design rests on. Writing the shipped text into `value` would store a
    copy of it on the next change event, and this installation would stop tracking the default."""
    source = REWRITING_JS.read_text()
    assert "field.placeholder = shipped" in source
    assert "field.value = shipped" not in source


def test_reset_clears_the_field_rather_than_writing_the_shipped_text_back() -> None:
    source = REWRITING_JS.read_text()
    assert 'config.patch({ [path]: "" })' in source


def test_the_polish_settings_left_the_context_tab(elements) -> None:
    """The Context tab is about what the assistant is given; this is about what it writes."""
    panels: dict[str, list[str]] = {}
    current: str | None = None
    for _tag, attrs in elements:
        if "data-settings-panel" in attrs:
            current = attrs["data-settings-panel"]
        if current and "data-config" in attrs:
            panels.setdefault(current, []).append(attrs["data-config"])

    assert not [path for path in panels.get("context", []) if path.startswith("polish.")]
    assert "polish.enabled" in panels["rewriting"]
    assert "dictation.instructions" in panels["rewriting"]


def test_the_guard_switches_are_all_present(elements) -> None:
    """Each guard enforces one shipped instruction after the fact, and will therefore reverse the
    same instruction once somebody rewrites it. Every one of them needs a way off."""
    bound = {attrs["data-config"] for _, attrs in _with(elements, "data-config")}
    assert {
        "polish.collapse_paragraphs",
        "polish.strip_decoration",
        "polish.reconcile_timestamps",
        "polish.min_retained_ratio",
        "polish.max_expansion_ratio",
        "dictation.max_expansion_ratio",
    } <= bound


def test_the_dictation_tidy_is_a_switch_over_a_two_valued_setting(elements) -> None:
    """`dictation.cleanup` stores "llm" or "off". The question the user answers is a yes or no, and
    a two-option dropdown would be the schema showing through the interface."""
    toggles = _with(elements, "data-config-boolean")
    assert [attrs["data-config"] for _, attrs in toggles] == ["dictation.cleanup"]
    assert toggles[0][1]["data-config-boolean"] == "llm|off"
    assert toggles[0][1].get("type") == "checkbox"
    assert "configBoolean" in BINDINGS_JS.read_text(), "the binding layer understands it"
