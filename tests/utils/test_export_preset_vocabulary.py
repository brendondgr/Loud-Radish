"""The export vocabulary is defined twice and must stay identical (D-037).

``web/backend/app/services/export/presets.py`` is canonical;
``web/frontend/static/js/core/export-presets.js`` mirrors it so the browser can import it without a
build step. Two hand-maintained copies of the same vocabulary drift, and the way they drift is
silent: "Balanced" meaning 720p on one side and 1080p on the other produces a window that describes
one file and an encoder that writes another, with nothing anywhere reporting a fault.

The same arrangement `test_mode_vocabulary.py` holds `modes.js` under, for the same reason and by
the same means — the JavaScript is read as text and compared to the Python, because parsing it with
a regular expression rather than a JavaScript engine keeps the suite free of the Node toolchain that
Decision D-011 removed on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.paths import STATIC_DIR
from app.services.export import job as job_module
from app.services.export import presets, runner

PRESETS_JS: Path = STATIC_DIR / "js" / "core" / "export-presets.js"


@pytest.fixture(scope="module")
def js() -> str:
    assert PRESETS_JS.is_file(), f"The mirrored vocabulary is missing: {PRESETS_JS}"
    return PRESETS_JS.read_text(encoding="utf-8")


def _constants(source: str) -> dict[str, str]:
    """``export const NAME = "value";`` → ``{"NAME": "value"}``."""
    return dict(re.findall(r'export const (\w+) = "([^"]*)";', source))


def _array(source: str, name: str) -> list[str]:
    match = re.search(rf"export const {name} = Object\.freeze\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"{name} is missing from export-presets.js"
    return [item.strip().strip('"') for item in match.group(1).split(",") if item.strip()]


def _resolve(items: list[str], constants: dict[str, str]) -> list[str]:
    return [constants.get(item, item) for item in items]


def test_the_two_sides_offer_the_same_presets_in_the_same_order(js: str) -> None:
    """Order matters as much as membership: it is the order the window draws them in."""
    mirrored = _resolve(_array(js, "EXPORT_PRESETS"), _constants(js))

    assert mirrored == [plan.id for plan in presets.PRESETS]


def test_the_two_sides_agree_on_the_default(js: str) -> None:
    constants = _constants(js)
    match = re.search(r"export const DEFAULT_PRESET = (\w+);", js)
    assert match, "DEFAULT_PRESET is missing from export-presets.js"

    assert constants[match.group(1)] == presets.DEFAULT_PRESET.id


def test_the_mirror_names_no_preset_the_backend_does_not_have(js: str) -> None:
    """A dead id in the mirror is a button that starts an export the server refuses."""
    for preset_id in _resolve(_array(js, "EXPORT_PRESETS"), _constants(js)):
        assert presets.by_id(preset_id) is not None, preset_id


def test_the_two_sides_agree_on_the_stages(js: str) -> None:
    """The window draws one row per stage, keyed by id. A missing key is a row that never fills."""
    mirrored = _resolve(_array(js, "EXPORT_STAGES"), _constants(js))
    source = _stage_ids_in_runner()

    assert mirrored == source


def test_the_two_sides_agree_on_the_job_states(js: str) -> None:
    mirrored = set(_resolve(_array(js, "EXPORT_STATES"), _constants(js)))

    assert mirrored == {str(state) for state in job_module.ExportState}


def test_the_two_sides_agree_on_the_stage_states(js: str) -> None:
    """`skipped` in particular: a copy never encodes, and a row that flashed to done would lie."""
    mirrored = set(_array(js, "STAGE_STATES"))

    assert mirrored == {str(state) for state in job_module.StageState}


def _stage_ids_in_runner() -> list[str]:
    """The stage ids `ExportRunner.plan_job` builds, in the order it builds them.

    Read out of a real job rather than out of the source text: the runner is the thing that decides,
    and a test that re-listed them by hand would be a third copy of the vocabulary.
    """
    source = _profile_of_anything()
    job, _plan = runner.ExportRunner(registry=job_module.ExportRegistry(), emit=_ignore).plan_job(
        key="k", preset_id=presets.DEFAULT_PRESET.id, source=source
    )
    return [stage.id for stage in job.stages]


def _profile_of_anything():  # noqa: ANN202 - returns SourceProfile
    from app.services.export.profile import SourceProfile

    return SourceProfile(
        path="/x.webm",
        width=1280,
        height=720,
        frame_rate=15.0,
        duration_s=60.0,
        size_bytes=10_000_000,
        video_codec="vp8",
        audio_codec="opus",
    )


def _ignore(_event: str, _payload: dict) -> None:
    return None
