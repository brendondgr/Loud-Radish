"""Deciding what size a recording should be written at.

Kept apart from the pipeline because this is the arithmetic that has gone wrong twice, in opposite
directions, and it deserves to be checkable on its own. A caps *range* handed to `videoscale` once
resolved to **480x16** — a sixteen-pixel-tall strip of a talk, written by a well-formed command that
exited zero. Fixing that by refusing to scale whenever the size was unknown was safe and abandoned
the resolution ceiling entirely, because the portal on this desktop reports no size at all.

The research (`docs/plans/capture-research-findings.md` §2) settles where the truth lives: **not in
the portal**. Its `size` property is optional *and* is specified in the compositor's logical
coordinate space, explicitly not pixels and explicitly permitted to differ from the stream's own
size — so on any scaled display it is the wrong number even when present. The only authoritative
dimensions are the ones GStreamer negotiates, read from a downstream CAPS event.

Everything here is pure. Feed it what the caps said; it returns what to record at, or refuses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

#: Smallest dimension worth recording. Below this the file is not a recording of anything — the
#: 480x16 file was sixteen pixels tall and technically valid, which is precisely the problem.
MIN_DIMENSION: Final = 16

#: Largest dimension to accept. Well above any real display, and low enough that a garbage value
#: from a failed negotiation is caught rather than propagated into an encoder.
MAX_DIMENSION: Final = 16384


class GeometryError(ValueError):
    """The negotiated size cannot be recorded. The message names the value that was wrong."""


@dataclass(frozen=True)
class Geometry:
    """A size to record at, and whether scaling is needed to reach it."""

    width: int
    height: int
    #: False when the source is already at or under the ceiling, so no scaler is needed at all.
    scaled: bool

    @property
    def caps(self) -> str:
        """The capsfilter this geometry corresponds to."""
        return f"video/x-raw,width={self.width},height={self.height}"


def even(value: int) -> int:
    """Round down to an even number.

    **Mandatory, not tidiness.** H.264, HEVC and AV1 all subsample chroma 4:2:0 and require even
    width and height; VA-API encoders are stricter about it than software ones. The current
    recordings happen to be even because a window happened to be 1080x1064 — window sizes are
    arbitrary and 1081x1063 is just as likely.
    """
    return value - (value & 1)


def resolve(
    source_width: int,
    source_height: int,
    *,
    max_height: int,
    max_width: int = MAX_DIMENSION,
) -> Geometry:
    """What to record a source of this size at, capped at ``max_height``.

    Only ever scales *down*: a window smaller than the ceiling is sharper at its own size than
    stretched up to one it never reached.

    Raises:
        GeometryError: when the source size is absent, degenerate, or absurd. **Refusing is the
            point.** Every fault this module exists for produced a plausible file from an
            implausible number, so an implausible number must not be allowed to reach an encoder.
    """
    for name, value in (("width", source_width), ("height", source_height)):
        if value < MIN_DIMENSION or value > MAX_DIMENSION:
            raise GeometryError(
                f"The negotiated source {name} is {value}, which is outside "
                f"{MIN_DIMENSION}–{MAX_DIMENSION}. Refusing to record rather than writing a file "
                f"nobody can watch."
            )

    ceiling_h = max(MIN_DIMENSION, min(max_height, MAX_DIMENSION))
    ceiling_w = max(MIN_DIMENSION, min(max_width, MAX_DIMENSION))

    if source_height <= ceiling_h and source_width <= ceiling_w:
        width, height = even(source_width), even(source_height)
        return Geometry(
            width=width, height=height, scaled=(width, height) != (source_width, source_height)
        )

    # Float arithmetic, then round. The reported `videoscale` integer overflow is the signature of
    # doing this in integers against a zero or absurd divisor — which the bounds check above has
    # already made impossible, but computing it this way removes the failure mode rather than
    # relying on a guard elsewhere staying correct.
    scale = min(ceiling_h / source_height, ceiling_w / source_width)
    height = even(max(MIN_DIMENSION, round(source_height * scale)))
    width = even(max(MIN_DIMENSION, round(source_width * scale)))
    return Geometry(width=width, height=height, scaled=True)


def describe(source_width: int, source_height: int, geometry: Geometry) -> str:
    """One line for the log, so a wrong-looking recording can be traced to a decision."""
    if not geometry.scaled:
        return f"recording at the source's own {geometry.width}x{geometry.height}"
    return f"scaling {source_width}x{source_height} down to {geometry.width}x{geometry.height}"
