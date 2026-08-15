"""Draw one frame of the Aperture microphone (docs/motion-spec.md).

The tray draws *frames*, not stylesheets — a `@keyframes` rule cannot be handed to D-Bus — which is
why the specification's closed-form amplitudes matter here as much as its CSS does. Given a visual
state and a time, this produces the thirteen bar heights and the furniture around them.

Rendered as **SVG text**. Every consumer needs something different from it (a D-Bus pixmap, a file,
a test assertion), and SVG is the one form all three can be derived from without this module
knowing which. It also keeps the renderer free of an image library, which matters on a machine where
the tray helper must start in milliseconds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from .visual_states import VisualState

#: The thirteen elements, from the specification: offset from centre, and full height.
GEOMETRY: Final[tuple[tuple[float, float], ...]] = (
    (-42, 37.6),
    (-35, 59.8),
    (-28, 73.0),
    (-21, 81.8),
    (-14, 87.6),
    (-7, 91.0),
    (0, 92.0),
    (7, 91.0),
    (14, 87.6),
    (21, 81.8),
    (28, 73.0),
    (35, 59.8),
    (42, 37.6),
)

BAR_WIDTH: Final = 4.4
CENTRE_X: Final = 100.0
CENTRE_Y: Final = 88.0

#: The capsule outline, verbatim from the specification.
CAPSULE: Final = (
    "M40 78 V94 A60 60 0 0 0 160 94 V78",
    "M100 150 V194",
    "M68 200 H132",
    "M74 200 C74 196 78 194 84 194 H116 C122 194 126 196 126 200",
)

#: Which bars survive in the fault state, and how tall the stubs are.
FAULT_BARS: Final[tuple[float, ...]] = (-21, -7, 7, 21)
FAULT_STUB_HEIGHT: Final = 6.0

LED_POSITIONS: Final[tuple[float, float, float]] = (86.0, 100.0, 114.0)
LED_Y: Final = 211.0
LED_UNLIT: Final = "#CFD3D6"


@dataclass(frozen=True)
class Frame:
    """One rendered frame."""

    svg: str
    #: The bar amplitudes that produced it, 0–1, for tests and for a raster renderer.
    amplitudes: tuple[float, ...]


def amplitude(index: int, t: float, motion: str, intensity: float = 1.0) -> float:
    """One bar's height as a fraction of its full extent.

    The specification's closed forms, for a renderer driving bars from a clock rather than from CSS.
    """
    if motion == "wave":
        a = abs(math.sin(t * 2.4 - index * 0.52)) * 0.52
        b = abs(math.sin(t * 1.1 + index * 0.21)) * 0.26
        return 0.16 + (a + b) * intensity
    if motion == "hold":
        return 0.5 + abs(math.sin(t * 3.1 - index * 0.34)) * 0.4 * intensity
    if motion == "settle":
        # The read head sweeping the capsule: a narrow travelling bump, not a whole-grille motion.
        sweep = max(0.0, 1.0 - abs(((t * 5.2) % 26) - 6 - index) * 0.55)
        return 0.1 + sweep * 0.34 * intensity
    if motion == "ripple":
        return 0.22 + (math.sin(t * 1.6 - index * 0.46) * 0.5 + 0.5) * 0.34 * intensity
    if motion == "stubs":
        return 0.07 if index % 3 == 1 else 0.05
    # `breathe`, and anything unrecognised. Idle is the safe default: it is the state that claims
    # least about what the application is doing.
    return 0.145 + math.sin(t * 1.15 - index * 0.3) * 0.045


def render(visual: VisualState, t: float, *, size: int = 22, intensity: float = 1.0) -> Frame:
    """One frame of the instrument, as SVG.

    Args:
        size: the rendered square, in pixels. **22 is the size that actually matters** — a tray
            icon — and is the one a 200×224 viewBox most easily fails at, which is why it is the
            default rather than something flattering.
    """
    amplitudes = tuple(
        amplitude(index, t, visual.motion, intensity) for index in range(len(GEOMETRY))
    )
    frame_colour = "#8A8F95" if visual.name in ("idle", "fault", "server-down") else "#5D6469"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 224" '
        f'width="{size}" height="{size}">',
        f'<g stroke="{frame_colour}" fill="none" stroke-width="2.4" stroke-linecap="round">',
        f'<circle cx="{CENTRE_X}" cy="{CENTRE_Y}" r="54"/>',
    ]
    parts += [f'<path d="{path}"/>' for path in CAPSULE]
    parts.append("</g>")

    parts.append(_grille(visual, amplitudes))
    parts.append(_furniture(visual, t))
    parts.append(_leds(visual))
    parts.append("</svg>")

    return Frame(svg="".join(parts), amplitudes=amplitudes)


def _grille(visual: VisualState, amplitudes: tuple[float, ...]) -> str:
    """The thirteen elements, which are both the grille and the meter."""
    # Fault collapses to four flat stubs, which is the picture rather than a low amplitude.
    if visual.motion == "stubs":
        bars = [
            f'<rect x="{CENTRE_X + dx - BAR_WIDTH / 2:.2f}" '
            f'y="{CENTRE_Y - FAULT_STUB_HEIGHT / 2:.2f}" '
            f'width="{BAR_WIDTH}" height="{FAULT_STUB_HEIGHT}" rx="2.2"/>'
            for dx in FAULT_BARS
        ]
        slash = (
            f'<path d="M58 46 L142 130" stroke="{visual.hue}" stroke-width="2.6" '
            f'stroke-linecap="round" opacity="0.8" fill="none"/>'
        )
        return f'<g fill="#B4B8BC">{"".join(bars)}</g>{slash}'

    bars = []
    for (dx, height), value in zip(GEOMETRY, amplitudes, strict=True):
        drawn = max(1.0, height * value)
        bars.append(
            f'<rect x="{CENTRE_X + dx - BAR_WIDTH / 2:.2f}" y="{CENTRE_Y - drawn / 2:.2f}" '
            f'width="{BAR_WIDTH}" height="{drawn:.2f}" rx="2.2"/>'
        )
    # Transcribing keeps the grille neutral: the hue belongs to the read head, because the mic is
    # no longer listening — it is looking.
    fill = "#9BA0A5" if visual.name == "transcribing" else visual.hue
    return f'<g fill="{fill}">{"".join(bars)}</g>'


def _furniture(visual: VisualState, t: float) -> str:
    """Whatever the state has beyond the grille: haloes, a read head, orbiting arcs."""
    if visual.name == "live":
        # Two rings expanding outward on every phrase, the second half a cycle behind.
        rings = []
        for offset in (0.0, 0.5):
            phase = ((t / 2.6) + offset) % 1.0
            rings.append(
                f'<circle cx="{CENTRE_X}" cy="{CENTRE_Y}" r="{56 + phase * 40:.1f}" fill="none" '
                f'stroke="{visual.hue}" stroke-width="1.2" opacity="{(1 - phase) * 0.5:.2f}"/>'
            )
        return "".join(rings)

    if visual.name == "recording":
        pulse = 0.14 + (math.sin(t * 2 * math.pi / 2.2) * 0.5 + 0.5) * 0.36
        return (
            f'<circle cx="{CENTRE_X}" cy="{CENTRE_Y}" r="66" fill="{visual.hue}" '
            f'opacity="{pulse:.2f}"/>'
        )

    if visual.name == "transcribing":
        # The read head sweeping left to right: ±46 px on a 2.4 s cycle.
        x = CENTRE_X + math.sin(t * 2 * math.pi / 2.4) * 46
        return (
            f'<g><rect x="{x - 4:.1f}" y="40" width="8" height="96" rx="4" fill="{visual.hue}" '
            f'opacity="0.55"/><rect x="{x - 1:.1f}" y="40" width="2" height="96" '
            f'fill="{visual.hue}"/></g>'
        )

    if visual.name == "rewriting":
        outer = (t / 5.5 % 1.0) * 360
        inner = 360 - (t / 7.5 % 1.0) * 360
        return (
            f'<g transform="rotate({outer:.1f} {CENTRE_X} {CENTRE_Y})">'
            f'<path d="M100 22 A66 66 0 0 1 157 55" fill="none" stroke="{visual.hue}" '
            f'stroke-width="2.2" stroke-linecap="round"/></g>'
            f'<g transform="rotate({inner:.1f} {CENTRE_X} {CENTRE_Y})">'
            f'<path d="M100 154 A66 66 0 0 1 43 121" fill="none" stroke="{visual.hue}" '
            f'stroke-width="1.4" stroke-linecap="round" opacity="0.7"/></g>'
        )

    return ""


def _leds(visual: VisualState) -> str:
    """Three lamps on the base — the redundant signal that keeps hue from being the only one."""
    circles = [
        f'<circle cx="{x}" cy="{LED_Y}" r="2.4" fill="{visual.hue if lit else LED_UNLIT}"/>'
        for x, lit in zip(LED_POSITIONS, visual.leds, strict=True)
    ]
    return f"<g>{''.join(circles)}</g>"
