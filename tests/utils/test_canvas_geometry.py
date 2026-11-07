"""The fixed canvas every window recording is written at (D-080).

Pure arithmetic, checked on its own because it is the one number every frame of a recording is
fitted to: a wrong one here is a wrong size for the whole of every recording.
"""

from __future__ import annotations

import pytest
from app.services.capture.geometry import CANVASES, DEFAULT_CANVAS, canvas


@pytest.mark.parametrize(
    ("name", "size"), [("720p", (1280, 720)), ("1080p", (1920, 1080)), ("1440p", (2560, 1440))]
)
def test_each_canvas_is_the_size_its_name_says(name: str, size: tuple[int, int]) -> None:
    geometry = canvas(name)
    assert (geometry.width, geometry.height) == size


def test_every_canvas_is_even_and_16_by_9() -> None:
    """Even for 4:2:0 chroma; 16:9 because that is the shape of the screens it is watched on."""
    for name in CANVASES:
        geometry = canvas(name)
        assert geometry.width % 2 == 0 and geometry.height % 2 == 0
        assert geometry.width * 9 == geometry.height * 16


def test_an_unknown_name_gets_the_default() -> None:
    assert canvas("4k") == canvas(DEFAULT_CANVAS)
    assert (canvas(DEFAULT_CANVAS).width, canvas(DEFAULT_CANVAS).height) == (1920, 1080)


def test_the_canvas_caps_state_both_numbers() -> None:
    """Never a range: fixation is what produced the 480x16 recording."""
    assert canvas("720p").caps == "video/x-raw,width=1280,height=720"


def test_the_config_offers_exactly_the_canvases_there_are() -> None:
    """The setting and the table must not drift: a value the table lacks silently gets 1080p."""
    import typing

    from app.config.schema import CaptureConfig

    offered = set(typing.get_args(CaptureConfig.model_fields["canvas"].annotation))
    assert offered == set(CANVASES)
    assert CaptureConfig().canvas == DEFAULT_CANVAS
