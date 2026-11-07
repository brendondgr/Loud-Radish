"""One drawn icon set for templates and modules (D-083).

`macros/icons.html`'s `icon()` macro and `static/js/core/icons.js` hold the same paths, because a
Jinja template cannot import a module and a module cannot import a template. This keeps the two
copies from drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

FRONTEND = Path(__file__).resolve().parents[2] / "web" / "frontend"
MODULE = FRONTEND / "static" / "js" / "core" / "icons.js"
TEMPLATES = FRONTEND / "templates"

ENTRY = re.compile(r"""^\s*"?([\w-]+)"?:\s*'(.*)',$""", re.M)


def module_paths() -> dict[str, str]:
    return dict(ENTRY.findall(MODULE.read_text()))


def macro_paths() -> dict[str, str]:
    source = (TEMPLATES / "macros" / "icons.html").read_text()
    block = source[source.index("{% set _paths") : source.index("} %}")]
    return dict(ENTRY.findall(block))


def test_the_template_and_the_module_draw_the_same_icons() -> None:
    assert module_paths(), "no icons found in core/icons.js; its shape must have changed"
    assert macro_paths() == module_paths()


def test_the_designs_icons_are_all_there() -> None:
    wanted = {"video", "audio", "transcript", "main", "folder", "download", "webapp", "more"}
    wanted |= {"trash", "spark", "book", "record", "mic", "shield", "grid", "send"}
    assert wanted <= set(macro_paths())


def test_the_macro_renders_a_decorative_svg() -> None:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    source = '{% import "macros/icons.html" as icons %}{{ icons.icon("send", 18) }}'
    html = env.from_string(source).render()

    assert html.startswith("<svg") and 'aria-hidden="true"' in html
    assert 'width="18"' in html and "M2 8h10.5" in html
