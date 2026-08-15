"""Drawing the instrument (docs/motion-spec.md).

The tray draws frames rather than running a stylesheet, so the specification's closed-form
amplitudes are the thing under test — a CSS `@keyframes` rule cannot be handed to D-Bus.

Amplitudes are checked against the ranges the specification declares. Getting one wrong does not
crash anything; it produces an instrument that moves in a way the design does not describe, which
nothing but a test would notice.
"""

from __future__ import annotations

import pytest
from app.companion.aperture import GEOMETRY, amplitude, render
from app.companion.visual_states import VISUALS


def test_there_are_thirteen_elements() -> None:
    assert len(GEOMETRY) == 13


def test_the_grille_is_symmetric_about_its_centre() -> None:
    heights = [height for _, height in GEOMETRY]
    assert heights == heights[::-1]
    offsets = [dx for dx, _ in GEOMETRY]
    assert offsets == [-dx for dx in reversed(offsets)]


def test_the_centre_element_is_the_tallest() -> None:
    heights = [height for _, height in GEOMETRY]
    assert heights[6] == max(heights)


@pytest.mark.parametrize("state", list(VISUALS))
def test_every_state_renders(state: str) -> None:
    frame = render(VISUALS[state], 1.0)
    assert frame.svg.startswith("<svg")
    assert frame.svg.endswith("</svg>")
    assert len(frame.amplitudes) == 13


@pytest.mark.parametrize("state", list(VISUALS))
def test_no_amplitude_leaves_the_capsule(state: str) -> None:
    """Above 1.0 a bar would draw outside the grille it is part of."""
    for t in (0.0, 0.4, 1.7, 3.3, 9.9):
        for value in render(VISUALS[state], t).amplitudes:
            assert 0.0 <= value <= 1.0, f"{state} at t={t}"


# -- the declared ranges, per state -----------------------------------------------------


def sample(motion: str, count: int = 200) -> list[float]:
    return [amplitude(i, t / 10, motion) for t in range(count) for i in range(13)]


def test_idle_breathes_between_thirteen_and_twenty_three_percent() -> None:
    """ "Fully desaturated, 13–23% travel" — the quietest thing the instrument does."""
    values = sample("breathe")
    assert 0.09 <= min(values) <= 0.15
    assert 0.18 <= max(values) <= 0.24


def test_recording_holds_high() -> None:
    """It pulses as one body — capturing, not reading — so it never drops to idle levels."""
    values = sample("hold")
    assert min(values) >= 0.45, "the grille dropped out of its held range"
    assert max(values) <= 0.95


def test_transcribing_falls_quiet() -> None:
    """The grille goes still and the read head carries the motion: the mic is looking, not
    listening."""
    values = sample("settle")
    assert max(values) <= 0.5


def test_the_live_wave_travels_rather_than_pulsing_together() -> None:
    """A travelling sine, not a whole-grille pulse — reading rather than capturing."""
    at_one_instant = [amplitude(i, 1.0, "wave") for i in range(13)]
    assert len(set(round(v, 2) for v in at_one_instant)) > 4, "every bar moved together"


def test_recording_pulses_together_rather_than_travelling() -> None:
    """The converse, and the reason the two are different pictures."""
    live_spread = max(amplitude(i, 1.0, "wave") for i in range(13)) - min(
        amplitude(i, 1.0, "wave") for i in range(13)
    )
    hold_spread = max(amplitude(i, 1.0, "hold") for i in range(13)) - min(
        amplitude(i, 1.0, "hold") for i in range(13)
    )
    assert hold_spread < live_spread


def test_the_fault_state_is_four_flat_stubs() -> None:
    frame = render(VISUALS["fault"], 1.0)
    assert frame.svg.count("<rect") == 4, "the fault grille should collapse to four stubs"
    assert "M58 46 L142 130" in frame.svg, "the diagonal slash is missing"


# -- the furniture each state carries ------------------------------------------------------


def test_live_draws_expanding_haloes() -> None:
    assert render(VISUALS["live"], 1.0).svg.count("<circle") >= 3


def test_transcribing_draws_a_read_head_and_a_neutral_grille() -> None:
    svg = render(VISUALS["transcribing"], 1.0).svg
    assert "#9BA0A5" in svg, "the grille should go neutral — the hue belongs to the read head"


def test_the_read_head_actually_moves() -> None:
    early = render(VISUALS["transcribing"], 0.0).svg
    later = render(VISUALS["transcribing"], 1.2).svg
    assert early != later


def test_rewriting_draws_two_counter_rotating_arcs() -> None:
    svg = render(VISUALS["rewriting"], 1.0).svg
    assert svg.count("rotate(") == 2


def test_the_arcs_rotate_in_opposite_directions() -> None:
    import re

    def angles(t: float) -> list[float]:
        svg = render(VISUALS["rewriting"], t).svg
        return [float(m) for m in re.findall(r"rotate\(([\d.]+)", svg)]

    first, second = angles(0.1), angles(1.0)
    assert (second[0] - first[0]) * (second[1] - first[1]) < 0, "both arcs turned the same way"


# -- the size that actually matters ----------------------------------------------------------


def test_it_renders_at_tray_size() -> None:
    """22 px is the size a tray icon actually is, and the one a 200x224 viewBox most easily
    fails at."""
    frame = render(VISUALS["recording"], 1.0, size=22)
    assert 'width="22"' in frame.svg
    assert 'height="22"' in frame.svg


def test_the_leds_are_a_second_signal_beside_the_hue() -> None:
    """Colour is never the only signal — the design system's rule and WCAG's."""
    idle = render(VISUALS["idle"], 1.0).svg
    recording = render(VISUALS["recording"], 1.0).svg
    assert idle.count("#CFD3D6") == 3, "idle should light no lamps"
    assert recording.count("#CFD3D6") == 2, "recording should light exactly one"
