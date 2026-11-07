"""The recordings lab page (D-080), as served.

There is no build step, so a misspelt import is found by a browser and nobody else. These check
that every module the lab imports exists, that every stylesheet the page links is served, and that
the panel offers no option the page cannot apply.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.config import ConfigStore
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[2] / "web" / "frontend" / "static"
LABS = STATIC / "js" / "labs"
IMPORT = re.compile(r"""^\s*import\s[^;]*?from\s+["'](\.[^"']+)["']""", re.MULTILINE | re.DOTALL)


@pytest.fixture(scope="module")
def pages(tmp_path_factory) -> dict[str, str]:
    from app.main import create_app

    store = ConfigStore(config_path=tmp_path_factory.mktemp("labs") / "config.json")
    found = {}
    with TestClient(create_app(config=store)) as client:
        for path in ("/labs/sessions/", "/labs/main/"):
            html = client.get(path).text
            for sheet in re.findall(r'href="(/static/css/labs/[^"]+)"', html):
                assert client.get(sheet).status_code == 200, sheet
            found[path] = html
    return found


@pytest.mark.parametrize("module", sorted(LABS.glob("*.js")), ids=lambda path: path.name)
def test_every_import_in_the_lab_resolves(module: Path) -> None:
    for target in IMPORT.findall(module.read_text(encoding="utf-8")):
        assert (module.parent / target).resolve().is_file(), f"{module.name} imports {target}"


def test_the_recordings_lab_has_somewhere_to_draw_each_part(pages) -> None:
    page = pages["/labs/sessions/"]
    for hook in (
        "data-lab-toolbar",
        "data-lab-list",
        "data-lab-inspector",
        "data-lab-detail",
        "data-lab-panel",
        "data-transcribe-consent",
    ):
        assert hook in page, hook


def test_every_layout_has_styles_and_a_toolbar_icon() -> None:
    options = (LABS / "options.js").read_text(encoding="utf-8")
    icons = (LABS / "icons.js").read_text(encoding="utf-8")
    css = "".join(
        path.read_text(encoding="utf-8") for path in (STATIC / "css" / "labs").glob("*.css")
    )
    layouts = re.findall(
        r'id: "(\w+)",\n\s+label: "[^"]+",\n\s+hint:', options.split("SECTIONS")[0]
    )

    assert layouts == ["gallery", "tiles", "wide", "timeline", "split", "list"]
    for layout in layouts:
        assert f'"layout-{layout}"' in icons, layout
        # Gallery is the base card every other layout overrides; the timeline is gallery cards
        # with date tiles between them.
        styled = {"gallery": True, "timeline": ".lg-marker" in css}
        assert styled.get(layout, f'data-layout="{layout}"' in css), layout


def test_the_main_screen_lab_draws_apart_from_its_own_controls(pages) -> None:
    page = pages["/labs/main/"]
    for hook in ("data-lab-stage", "data-lab-bar", "data-lab-panel", "/static/js/labs/main.js"):
        assert hook in page, hook


def test_the_labs_keep_the_application_surfaces() -> None:
    """Radish is the accent only (owner, second round): no lab may tint the page or its cards."""
    theme = (STATIC / "css" / "labs" / "theme.css").read_text(encoding="utf-8")
    assert "--lab-bg: var(--surface-base);" in theme
    assert "data-tint" not in theme
