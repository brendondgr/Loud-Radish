"""The Aperture as pixels, for a tray that speaks ARGB32 rather than SVG.

`StatusNotifierItem` wants its icon as `a(iiay)` — width, height, and a buffer of ARGB32 bytes.
Nothing in this project turns SVG into pixels, and nothing can be added that does: `cairosvg` needs
a system cairo, and a GUI toolkit is a hundred megabytes for one 22-pixel picture. Both contradict
D-023's promise that `uv sync` is the whole install.

So this draws the instrument a second time, from the same geometry `aperture.py` draws it from, into
a `numpy` array — which is already a dependency. The two renderers share their constants and their
amplitude function, so they cannot drift on *what* is drawn; they differ only in what they draw it
with. The SVG one stays authoritative for anything a browser or a test reads.

**Everything is drawn at four times the size and averaged down.** A 22-pixel square rendered
directly has one bar per two pixels and no antialiasing, which at tray size is the difference
between an instrument and a smudge. Supersampling gives seventeen levels of coverage for the cost of
an 88×88 array, which is nothing.

Colours come back through the same oklch strings the specification uses, converted here rather than
pre-baked as hex: the spec is written in oklch, and a table of hex approximations is a second source
of truth that nobody would remember to update.
"""

from __future__ import annotations

import math
import re
from typing import Final

import numpy as np

from .aperture import (
    BAR_WIDTH,
    CENTRE_X,
    CENTRE_Y,
    FAULT_BARS,
    FAULT_STUB_HEIGHT,
    GEOMETRY,
    LED_POSITIONS,
    LED_UNLIT,
    LED_Y,
    amplitude,
)
from .visual_states import VisualState

#: The SVG's coordinate space. Every constant imported above is in these units.
VIEW_W: Final = 200.0
VIEW_H: Final = 224.0

#: Draw at this multiple and average down. Four is where the returns stop being visible at 22 px.
SUPERSAMPLE: Final = 4

#: The instrument's frame, when the state is doing nothing versus doing something.
FRAME_RESTING: Final = "#8A8F95"
FRAME_ACTIVE: Final = "#5D6469"

#: The grille when the hue belongs to something else — transcribing gives it to the read head.
GRILLE_NEUTRAL: Final = "#9BA0A5"

#: Fault's four stubs are this colour, and its slash takes the state hue.
FAULT_STUB_COLOUR: Final = "#B4B8BC"

_OKLCH = re.compile(
    r"oklch\(\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*\)",
    re.IGNORECASE,
)


class Pixmap:
    """One rendered icon: its size, and its ARGB32 bytes."""

    __slots__ = ("data", "height", "width")

    def __init__(self, width: int, height: int, data: bytes) -> None:
        self.width = width
        self.height = height
        self.data = data

    def __len__(self) -> int:
        return len(self.data)

    def as_sni_pixmap(self) -> tuple[int, int, bytes]:
        """The `(width, height, bytes)` struct an `a(iiay)` icon property is a list of."""
        return (self.width, self.height, self.data)


# -- colour ------------------------------------------------------------------------------


def parse_colour(value: str) -> tuple[float, float, float]:
    """A specification colour as linear-free sRGB floats in 0–1. Accepts `#rrggbb` and `oklch()`.

    Unrecognised input returns mid grey rather than raising: a tray icon that is the wrong grey is
    a cosmetic fault, and one that raises on a frame clock tick takes the whole companion with it.
    """
    text = value.strip()
    if text.startswith("#") and len(text) == 7:
        return tuple(int(text[i : i + 2], 16) / 255.0 for i in (1, 3, 5))  # type: ignore[return-value]

    match = _OKLCH.match(text)
    if match is None:
        return (0.5, 0.5, 0.5)
    lightness, chroma, hue_deg = (float(group) for group in match.groups())
    return _oklch_to_srgb(lightness, chroma, hue_deg)


def _oklch_to_srgb(lightness: float, chroma: float, hue_deg: float) -> tuple[float, float, float]:
    """Björn Ottosson's OKLab, then the sRGB transfer function. Clipped, not gamut-mapped.

    Clipping is the right trade here. A tray icon is 22 pixels of a colour chosen for recognition,
    not for reproduction, and a proper gamut mapping is a great deal of arithmetic to move a hue
    the user will never compare against anything.
    """
    hue = math.radians(hue_deg)
    a = chroma * math.cos(hue)
    b = chroma * math.sin(hue)

    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3

    linear = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )
    return tuple(_encode(channel) for channel in linear)  # type: ignore[return-value]


def _encode(channel: float) -> float:
    channel = min(1.0, max(0.0, channel))
    if channel <= 0.0031308:
        return channel * 12.92
    return 1.055 * (channel ** (1 / 2.4)) - 0.055


# -- the canvas --------------------------------------------------------------------------


class _Canvas:
    """A supersampled RGBA canvas in the SVG's coordinate space.

    Shapes are written as coverage masks over a coordinate grid rather than as scanline fills. That
    is what makes each one three lines of `numpy` instead of a rasteriser, and it is affordable
    because the whole canvas is 88 × 88.
    """

    def __init__(self, size: int, scale: int) -> None:
        self.pixels = size * scale
        # Square canvas over a non-square viewBox: the instrument is centred and the taller axis
        # decides the scale, so nothing is cropped.
        self.unit = max(VIEW_W, VIEW_H) / self.pixels
        offset_x = (max(VIEW_W, VIEW_H) - VIEW_W) / 2
        offset_y = (max(VIEW_W, VIEW_H) - VIEW_H) / 2
        axis = (np.arange(self.pixels) + 0.5) * self.unit
        self.x = (axis - offset_x)[None, :]
        self.y = (axis - offset_y)[:, None]
        self.rgb = np.zeros((self.pixels, self.pixels, 3), dtype=np.float32)
        self.alpha = np.zeros((self.pixels, self.pixels), dtype=np.float32)

    # -- primitives ----------------------------------------------------------------------

    def _distance_to_segment(self, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
        dx, dy = x1 - x0, y1 - y0
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            return np.hypot(self.x - x0, self.y - y0)
        t = ((self.x - x0) * dx + (self.y - y0) * dy) / length_sq
        t = np.clip(t, 0.0, 1.0)
        return np.hypot(self.x - (x0 + t * dx), self.y - (y0 + t * dy))

    def disc(self, cx: float, cy: float, radius: float) -> np.ndarray:
        return (np.hypot(self.x - cx, self.y - cy) <= radius).astype(np.float32)

    def ring(self, cx: float, cy: float, radius: float, width: float) -> np.ndarray:
        return (np.abs(np.hypot(self.x - cx, self.y - cy) - radius) <= width / 2).astype(np.float32)

    def arc(
        self, cx: float, cy: float, radius: float, width: float, start_deg: float, end_deg: float
    ) -> np.ndarray:
        """A ring, kept only between two angles measured clockwise from twelve o'clock."""
        angle = np.degrees(np.arctan2(self.x - cx, cy - self.y)) % 360.0
        span = (angle - start_deg) % 360.0
        within = span <= (end_deg - start_deg) % 360.0
        return self.ring(cx, cy, radius, width) * within.astype(np.float32)

    def stroke(self, x0: float, y0: float, x1: float, y1: float, width: float) -> np.ndarray:
        return (self._distance_to_segment(x0, y0, x1, y1) <= width / 2).astype(np.float32)

    def rounded_rect(self, x: float, y: float, w: float, h: float, radius: float) -> np.ndarray:
        radius = min(radius, w / 2, h / 2)
        # A rounded rectangle is the set of points within `radius` of its inset core.
        inner_x0, inner_x1 = x + radius, x + w - radius
        inner_y0, inner_y1 = y + radius, y + h - radius
        dx = np.maximum(np.maximum(inner_x0 - self.x, self.x - inner_x1), 0.0)
        dy = np.maximum(np.maximum(inner_y0 - self.y, self.y - inner_y1), 0.0)
        return (np.hypot(dx, dy) <= radius).astype(np.float32)

    # -- compositing ---------------------------------------------------------------------

    def paint(
        self, mask: np.ndarray, colour: tuple[float, float, float], opacity: float = 1.0
    ) -> None:
        """Source-over, on straight (non-premultiplied) colour."""
        coverage = (mask * opacity).astype(np.float32)
        if not coverage.any():
            return
        out_alpha = coverage + self.alpha * (1.0 - coverage)
        safe = np.where(out_alpha > 0, out_alpha, 1.0)[..., None]
        source = np.array(colour, dtype=np.float32)
        self.rgb = (
            source * coverage[..., None] + self.rgb * (self.alpha * (1.0 - coverage))[..., None]
        ) / safe
        self.alpha = out_alpha

    def to_argb32(self, size: int, scale: int) -> bytes:
        """Average the supersampled canvas down and pack it big-endian ARGB, one byte a channel."""
        shape = (size, scale, size, scale)
        alpha = self.alpha.reshape(shape).mean(axis=(1, 3))
        # Average *premultiplied* colour, or a transparent pixel's arbitrary colour bleeds into its
        # neighbours along every edge — which at this size is every pixel that matters.
        premultiplied = self.rgb * self.alpha[..., None]
        colour = premultiplied.reshape(*shape, 3).mean(axis=(1, 3))
        safe = np.where(alpha > 0, alpha, 1.0)[..., None]
        straight = np.clip(colour / safe, 0.0, 1.0)

        out = np.empty((size, size, 4), dtype=np.uint8)
        out[..., 0] = np.round(alpha * 255)
        out[..., 1:] = np.round(straight * 255)
        return out.tobytes()


# -- the instrument ----------------------------------------------------------------------


def render_pixmap(
    visual: VisualState, t: float, *, size: int = 22, intensity: float = 1.0
) -> Pixmap:
    """One frame of the Aperture as an ARGB32 pixmap.

    The same arguments as :func:`aperture.render`, and the same picture — the fault state is four
    stubs and a slash, transcribing keeps a neutral grille and gives the hue to its read head, and
    the frame darkens when the instrument is doing something.
    """
    canvas = _Canvas(size, SUPERSAMPLE)
    hue = parse_colour(visual.hue)
    resting = visual.name in ("idle", "fault", "server-down")
    frame = parse_colour(FRAME_RESTING if resting else FRAME_ACTIVE)

    _paint_furniture_under(canvas, visual, hue, t)
    _paint_frame(canvas, frame)
    _paint_grille(canvas, visual, hue, t, intensity)
    _paint_furniture_over(canvas, visual, hue, t)
    _paint_leds(canvas, visual, hue)

    return Pixmap(size, size, canvas.to_argb32(size, SUPERSAMPLE))


def _paint_frame(canvas: _Canvas, colour: tuple[float, float, float]) -> None:
    """The capsule, its stem and its base — the parts that never move."""
    canvas.paint(canvas.ring(CENTRE_X, CENTRE_Y, 54, 2.4), colour)
    # "M40 78 V94 A60 60 0 0 0 160 94 V78": two shoulders and the arc between them.
    canvas.paint(canvas.stroke(40, 78, 40, 94, 2.4), colour)
    canvas.paint(canvas.stroke(160, 78, 160, 94, 2.4), colour)
    canvas.paint(canvas.arc(CENTRE_X, 94, 60, 2.4, 90, 270), colour)
    canvas.paint(canvas.stroke(CENTRE_X, 150, CENTRE_X, 194, 2.4), colour)
    canvas.paint(canvas.stroke(68, 200, 132, 200, 2.4), colour)


def _paint_grille(
    canvas: _Canvas,
    visual: VisualState,
    hue: tuple[float, float, float],
    t: float,
    intensity: float,
) -> None:
    """The thirteen elements, which are both the grille and the meter."""
    if visual.motion == "stubs":
        stub = parse_colour(FAULT_STUB_COLOUR)
        for dx in FAULT_BARS:
            canvas.paint(
                canvas.rounded_rect(
                    CENTRE_X + dx - BAR_WIDTH / 2,
                    CENTRE_Y - FAULT_STUB_HEIGHT / 2,
                    BAR_WIDTH,
                    FAULT_STUB_HEIGHT,
                    2.2,
                ),
                stub,
            )
        canvas.paint(canvas.stroke(58, 46, 142, 130, 2.6), hue, 0.8)
        return

    colour = parse_colour(GRILLE_NEUTRAL) if visual.name == "transcribing" else hue
    for index, (dx, height) in enumerate(GEOMETRY):
        drawn = max(1.0, height * amplitude(index, t, visual.motion, intensity))
        canvas.paint(
            canvas.rounded_rect(
                CENTRE_X + dx - BAR_WIDTH / 2, CENTRE_Y - drawn / 2, BAR_WIDTH, drawn, 2.2
            ),
            colour,
        )


def _paint_furniture_under(
    canvas: _Canvas, visual: VisualState, hue: tuple[float, float, float], t: float
) -> None:
    """Whatever sits behind the grille: recording's pulsing body, live's expanding rings."""
    if visual.name == "recording":
        pulse = 0.14 + (math.sin(t * 2 * math.pi / 2.2) * 0.5 + 0.5) * 0.36
        canvas.paint(canvas.disc(CENTRE_X, CENTRE_Y, 66), hue, pulse)
        return

    if visual.name == "live":
        for offset in (0.0, 0.5):
            phase = ((t / 2.6) + offset) % 1.0
            canvas.paint(
                canvas.ring(CENTRE_X, CENTRE_Y, 56 + phase * 40, 1.2), hue, (1 - phase) * 0.5
            )


def _paint_furniture_over(
    canvas: _Canvas, visual: VisualState, hue: tuple[float, float, float], t: float
) -> None:
    """Whatever sits in front of it: the read head, the orbiting arcs."""
    if visual.name == "transcribing":
        x = CENTRE_X + math.sin(t * 2 * math.pi / 2.4) * 46
        canvas.paint(canvas.rounded_rect(x - 4, 40, 8, 96, 4), hue, 0.55)
        canvas.paint(canvas.rounded_rect(x - 1, 40, 2, 96, 1), hue)
        return

    if visual.name == "rewriting":
        outer = (t / 5.5 % 1.0) * 360
        inner = 360 - (t / 7.5 % 1.0) * 360
        canvas.paint(canvas.arc(CENTRE_X, CENTRE_Y, 66, 2.2, outer, outer + 60), hue)
        canvas.paint(canvas.arc(CENTRE_X, CENTRE_Y, 66, 1.4, inner + 180, inner + 240), hue, 0.7)


def _paint_leds(canvas: _Canvas, visual: VisualState, hue: tuple[float, float, float]) -> None:
    """Three lamps on the base — the redundant signal that keeps hue from being the only one."""
    unlit = parse_colour(LED_UNLIT)
    for x, lit in zip(LED_POSITIONS, visual.leds, strict=True):
        canvas.paint(canvas.disc(x, LED_Y, 2.4), hue if lit else unlit)
