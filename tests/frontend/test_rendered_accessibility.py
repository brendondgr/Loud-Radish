"""Structural accessibility rules, over the HTML the server actually renders (D-054).

`docs/design-system.md` has carried an unchecked "automated a11y check (axe or equivalent) —
tooling not yet selected" since it was written, and `docs/checklist.md` has said "not selected"
for as long. The reason is that every obvious option assumes a browser and an npm build step, and
this project deliberately has neither.

Two things can be checked without either, and they happen to be the two failures this codebase
actually risks — a template edit quietly dropping a label, and a live region losing the attribute
that makes it announce. `TestClient` already renders the real pages in `tests/api/`, so the HTML is
free; the parser is `html.parser` from the standard library, because adding BeautifulSoup to check
that inputs have labels would be the same trade this repository declined when it wrote its own
rasteriser rather than depend on Pillow.

**This is not a substitute for the manual passes.** It cannot see contrast in context, focus order,
or whether a label says anything useful. It catches regressions in the rules that can be stated.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from app.config import ConfigStore
from fastapi.testclient import TestClient


class Element:
    """One tag, its attributes, and the text directly inside it."""

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.text = ""
        #: The nearest enclosing `<label>`, if any. `<label><input/> Skip silence</label>` labels
        #: its input without a `for` attribute, and it is the pattern this codebase mostly uses.
        self.label: Element | None = None

    def __repr__(self) -> str:  # pragma: no cover - test failure output only
        shown = {k: v for k, v in self.attrs.items() if k in ("id", "class", "type", "name")}
        return f"<{self.tag} {shown}>"

    @property
    def named(self) -> bool:
        """Whether assistive technology would have something to call this."""
        return bool(
            self.text.strip()
            or self.attrs.get("aria-label", "").strip()
            or self.attrs.get("aria-labelledby", "").strip()
            or self.attrs.get("title", "").strip()
        )


class Collector(HTMLParser):
    """Every element, flat, with the text between its own tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[Element] = []
        self._open: list[Element] = []

    #: Tags that never have children, so they never go on the stack.
    VOID = frozenset(
        {"input", "img", "br", "hr", "meta", "link", "source", "area", "base", "col", "track"}
    )

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        element = Element(tag, {key: (value or "") for key, value in attrs})
        element.label = next(
            (open_ for open_ in reversed(self._open) if open_.tag == "label"), None
        )
        self.elements.append(element)
        if tag not in self.VOID:
            self._open.append(element)

    def handle_startendtag(self, tag: str, attrs) -> None:  # noqa: ANN001
        """`<path … />` and friends.

        **Overridden, and this was a real bug.** The default implementation calls `handle_starttag`
        and then `handle_endtag`, and the end-tag handler below unwinds the stack until it finds a
        match — so a self-closing `<path/>` inside an `<svg>` popped the svg, the button around it,
        and everything else, and every enclosing element lost the text that followed. The visible
        symptom was two buttons reported as having no accessible name when their markup plainly
        contained a `<span>` of text.
        """
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        if not any(element.tag == tag for element in self._open):
            return  # a stray close tag must not unwind everything above it
        while self._open:
            element = self._open.pop()
            if element.tag == tag:
                break

    def handle_data(self, data: str) -> None:
        for element in self._open:
            element.text += data


def parse(markup: str) -> list[Element]:
    collector = Collector()
    collector.feed(markup)
    return collector.elements


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, list[Element]]:
    from app.main import create_app

    store = ConfigStore(config_path=tmp_path_factory.mktemp("a11y") / "config.json")
    with TestClient(create_app(config=store)) as client:
        return {path: parse(client.get(path).text) for path in ("/", "/sessions")}


def of(elements: list[Element], *tags: str) -> list[Element]:
    return [element for element in elements if element.tag in tags]


# -- the page itself ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_the_page_declares_its_language(pages, path) -> None:
    """Without it a screen reader guesses, and reads English with the wrong phonemes."""
    [html] = of(pages[path], "html")

    assert html.attrs.get("lang"), f"{path} has no lang attribute"


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_the_page_has_exactly_one_top_level_heading(pages, path) -> None:
    headings = of(pages[path], "h1")

    assert len(headings) == 1, f"{path} has {len(headings)} h1 elements"


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_the_page_has_a_title(pages, path) -> None:
    [title] = of(pages[path], "title")

    assert title.text.strip()


# -- controls ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_every_button_has_an_accessible_name(pages, path) -> None:
    """**The regression this file is really for.** A button whose only content is an icon reads as
    "button" and nothing else, and an icon is exactly the kind of thing a redesign swaps in."""
    unnamed = [
        button
        for button in of(pages[path], "button")
        if not button.named and button.attrs.get("aria-hidden") != "true"
    ]

    assert not unnamed, f"buttons with no accessible name on {path}: {unnamed}"


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_every_input_can_be_named(pages, path) -> None:
    """A `for` label, a wrapping label, an `aria-label`, or a `title`. A placeholder is not a
    label: it disappears the moment anything is typed."""
    labelled = {
        element.attrs["for"] for element in of(pages[path], "label") if element.attrs.get("for")
    }
    orphans = [
        field
        for field in of(pages[path], "input", "select", "textarea")
        # `type="hidden"` carries no value a person enters. The `hidden` *attribute* is different
        # and also excluded: a file input triggered by a visible button is not focusable, and
        # demanding a label for it would be demanding one nobody can reach.
        if field.attrs.get("type") not in ("hidden",)
        and "hidden" not in field.attrs
        and field.attrs.get("id") not in labelled
        and not (field.label is not None and field.label.named)
        and not field.attrs.get("aria-label")
        and not field.attrs.get("aria-labelledby")
        and not field.attrs.get("title")
    ]

    assert not orphans, f"unlabelled fields on {path}: {orphans}"


@pytest.mark.parametrize("path", ["/", "/sessions"])
def test_every_image_declares_whether_it_means_anything(pages, path) -> None:
    """`alt=""` is a decision — "this is decoration, skip it". A missing `alt` is not."""
    silent = [image for image in of(pages[path], "img") if "alt" not in image.attrs]

    assert not silent, f"images with no alt on {path}: {silent}"


# -- the parts the design system requires ------------------------------------------------------


def test_the_transcript_is_a_polite_live_region(pages) -> None:
    """A transcript that fills in without announcing itself is a screen reader reading nothing
    while the room fills with words; one that announces *assertively* interrupts constantly."""
    regions = [
        element
        for element in pages["/"]
        if element.attrs.get("role") == "log" or element.attrs.get("aria-live")
    ]

    assert regions, "the transcript declares no live region at all"
    assert any(element.attrs.get("aria-live") == "polite" for element in regions)


def test_a_toggle_button_says_whether_it_is_on(pages) -> None:
    """`aria-pressed` is what makes a toggle a toggle rather than a button that does something."""
    toggles = [
        element
        for element in pages["/"]
        if element.tag == "button" and "aria-pressed" in element.attrs
    ]

    assert toggles, "no button reports its pressed state"
    assert all(element.attrs["aria-pressed"] in ("true", "false") for element in toggles)


def test_the_raw_transcript_toggle_is_reachable_and_named(pages) -> None:
    """Added in this plan; asserted here so a later template edit cannot quietly drop it."""
    [toggle] = [element for element in pages["/"] if "data-toggle-raw" in element.attrs]

    assert toggle.tag == "button"
    assert toggle.named
    assert toggle.attrs.get("aria-pressed") == "false"


def test_a_decorative_glyph_is_hidden_from_the_reader(pages) -> None:
    """Icon fonts and single letters used as pictures read as gibberish otherwise."""
    hidden = [element for element in pages["/"] if element.attrs.get("aria-hidden") == "true"]

    assert hidden, "nothing is marked decorative, which is unlikely in a page with icons"
