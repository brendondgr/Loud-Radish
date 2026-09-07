"""The Aperture as ARGB32 pixels, which is the only form a tray can be handed.

`aperture.py` is already covered by `test_aperture_render.py`, which asserts against SVG text. This
covers the second renderer: the same geometry drawn into a `numpy` array, because no SVG rasteriser
can be added without a system library or a GUI toolkit (D-042).

What matters is the shape of the buffer, that the states stay distinguishable at 22 pixels, and that
the antialiasing is real — a tray icon that aliases is a smudge, and pixel counts alone would not
notice.
"""

from __future__ import annotations

import pytest
from app.companion.aperture import GEOMETRY
from app.companion.raster import SUPERSAMPLE, parse_colour, render_pixmap
from app.companion.visual_states import VISUALS


def alpha_of(pixmap, x: int, y: int) -> int:
    return pixmap.data[(y * pixmap.width + x) * 4]


def rgb_of(pixmap, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * pixmap.width + x) * 4
    return tuple(pixmap.data[offset + 1 : offset + 4])


def ink(pixmap) -> int:
    return sum(1 for i in range(0, len(pixmap.data), 4) if pixmap.data[i] > 10)


# -- the buffer ----------------------------------------------------------------------------


def test_the_buffer_is_exactly_four_bytes_a_pixel() -> None:
    """`a(iiay)` carries the width and height beside the bytes, and a host trusts them. A buffer
    that disagrees with its own dimensions is read off the end."""
    pixmap = render_pixmap(VISUALS["idle"], 1.0, size=22)

    assert (pixmap.width, pixmap.height) == (22, 22)
    assert len(pixmap.data) == 22 * 22 * 4


@pytest.mark.parametrize("size", [16, 22, 24, 32, 48])
def test_every_tray_size_produces_a_matching_buffer(size: int) -> None:
    pixmap = render_pixmap(VISUALS["live"], 1.0, size=size)

    assert len(pixmap.data) == size * size * 4


def test_the_sni_struct_is_the_three_fields_in_order() -> None:
    pixmap = render_pixmap(VISUALS["idle"], 1.0, size=22)

    width, height, data = pixmap.as_sni_pixmap()

    assert (width, height) == (22, 22)
    assert data is pixmap.data


def test_the_corners_are_transparent() -> None:
    """The instrument is a circle on a stand. An icon with an opaque background is a tile, and a
    tray full of tiles is what a panel looks like when something has gone wrong."""
    pixmap = render_pixmap(VISUALS["recording"], 1.0, size=22)

    for x, y in ((0, 0), (21, 0), (0, 21), (21, 21)):
        assert alpha_of(pixmap, x, y) == 0


# -- that it is drawn at all ---------------------------------------------------------------


@pytest.mark.parametrize("name", list(VISUALS))
def test_every_visual_state_rasterises(name: str) -> None:
    """`visual_states.py` maps every (mode, run state) pair onto one of these. A state that raises
    here is one that kills the frame clock the first time a session enters it."""
    pixmap = render_pixmap(VISUALS[name], 1.0, size=22)

    assert ink(pixmap) > 20, "the instrument should be visible, not a few stray pixels"


@pytest.mark.parametrize("t", [0.0, 0.37, 1.0, 2.5, 7.9, 60.0])
def test_the_clock_never_produces_an_empty_frame(t: float) -> None:
    """Amplitudes are trigonometric and some of them cross zero. A bar of zero height must still
    be drawn — `aperture.py` floors it at one unit — or the grille blinks out mid-phrase."""
    pixmap = render_pixmap(VISUALS["live"], t, size=22)

    assert ink(pixmap) > 20


# -- that the states stay apart ------------------------------------------------------------


def test_the_two_recording_pictures_differ_as_pixels() -> None:
    """The whole reason `visual_states.py` is keyed on (mode, run state): live and recorded are the
    same run state and must not be the same picture. Distinct names are not enough if they
    rasterise the same."""
    live = render_pixmap(VISUALS["live"], 1.0, size=22)
    recorded = render_pixmap(VISUALS["recording"], 1.0, size=22)

    assert live.data != recorded.data


def test_no_two_states_rasterise_identically() -> None:
    frames = {name: render_pixmap(visual, 1.0, size=22).data for name, visual in VISUALS.items()}

    # `server-down` deliberately borrows fault's treatment, so those two may match.
    distinct = {name: data for name, data in frames.items() if name != "server-down"}
    assert len(set(distinct.values())) == len(distinct)


def test_recording_reads_red_and_live_reads_teal() -> None:
    """The hues come from the specification as oklch strings. A conversion that silently returned
    grey would leave every state looking like idle and no test of shape would notice."""
    live = rgb_of(render_pixmap(VISUALS["live"], 1.0, size=22), 11, 8)
    recording = rgb_of(render_pixmap(VISUALS["recording"], 1.0, size=22), 11, 8)

    assert live[2] > live[0] and live[1] > live[0], f"live should be teal, got {live}"
    assert recording[0] > recording[1] and recording[0] > recording[2], (
        f"recording should be red, got {recording}"
    )


def test_the_animation_moves() -> None:
    """A frame clock that produced identical frames would be a still picture with a thread behind
    it, and `NewIcon` would be emitted forever for nothing."""
    first = render_pixmap(VISUALS["live"], 0.0, size=22)
    later = render_pixmap(VISUALS["live"], 0.6, size=22)

    assert first.data != later.data


def test_a_held_state_does_not_move() -> None:
    """The same time must give the same frame, which is what lets the tray skip an unchanged one
    rather than signalling ten times a second."""
    assert render_pixmap(VISUALS["idle"], 2.0).data == render_pixmap(VISUALS["idle"], 2.0).data


# -- antialiasing --------------------------------------------------------------------------


def test_the_downsample_produces_partial_coverage() -> None:
    """The point of supersampling. Without it every pixel is 0 or 255 and the circle is a staircase
    — which is what a tray icon drawn straight at 22 px looks like."""
    pixmap = render_pixmap(VISUALS["idle"], 1.0, size=22)
    alphas = {pixmap.data[i] for i in range(0, len(pixmap.data), 4)}

    partial = {a for a in alphas if 0 < a < 255}
    assert len(partial) >= 8, f"expected a range of edge coverage, got {sorted(alphas)}"


def test_transparent_pixels_carry_no_colour_bleed() -> None:
    """Colour is averaged premultiplied. Averaged straight, a transparent pixel's arbitrary colour
    would drag every edge toward it — visible at this size as a dark halo."""
    pixmap = render_pixmap(VISUALS["recording"], 1.0, size=22)

    for i in range(0, len(pixmap.data), 4):
        if pixmap.data[i] == 0:
            assert pixmap.data[i + 1 : i + 4] == b"\x00\x00\x00"


def test_the_supersample_is_more_than_one() -> None:
    """Guards the constant itself: setting it to 1 silently removes every edge in the picture and
    only the test above would notice, indirectly."""
    assert SUPERSAMPLE >= 2


# -- colour ------------------------------------------------------------------------------


def test_hex_colours_round_trip() -> None:
    assert parse_colour("#8A8F95") == pytest.approx((0x8A / 255, 0x8F / 255, 0x95 / 255))


def test_oklch_becomes_the_colour_the_specification_names() -> None:
    """oklch(0.58 0.14 25) is the specification's recording red. Checked as a hue relationship
    rather than exact bytes, so a better gamut mapping later does not break the test."""
    r, g, b = parse_colour("oklch(0.58 0.14 25)")

    assert r > g > b
    assert 0.6 < r < 0.9


def test_an_unparseable_colour_is_grey_rather_than_an_exception() -> None:
    """This runs on a frame clock. Raising here stops the tray, and a wrong grey does not."""
    assert parse_colour("cornflowerblue") == (0.5, 0.5, 0.5)
    assert parse_colour("") == (0.5, 0.5, 0.5)


# -- the geometry it shares with the SVG renderer ------------------------------------------


def test_both_renderers_draw_the_same_number_of_bars() -> None:
    """The two renderers share `GEOMETRY` and `amplitude`, which is what stops them drifting on
    what the instrument *is*. This asserts the sharing rather than the drawing."""
    from app.companion.aperture import render

    frame = render(VISUALS["live"], 1.0)

    assert len(frame.amplitudes) == len(GEOMETRY) == 13
