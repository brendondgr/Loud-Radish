"""WCAG contrast over the design tokens, in pure Python (D-054).

`docs/design-system.md` sets a WCAG 2.1 AA baseline and records two corrections that were made
after measuring. Nothing has ever checked that they stay made. This does, with no dependency at
all — a contrast ratio is a dozen lines of arithmetic, and adding a browser and an accessibility
engine to compute one would be the trade this repository declined when it wrote its own rasteriser
rather than depend on Pillow.

**What this cannot do**, and the manual passes still must: see a colour composited over a
translucent layer, see a colour a stylesheet only applies under a media query, or judge whether the
contrast is *comfortable* rather than merely passing. It checks the pairs that are named below,
because a token pair is the thing a redesign changes by accident.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[2] / "web" / "frontend" / "static" / "css" / "tokens.css"

#: WCAG 2.1: 4.5 for body text, 3.0 for large text and for the boundary of a control.
AA_TEXT = 4.5
AA_LARGE = 3.0


def tokens() -> dict[str, str]:
    """Every `--name: #rrggbb` in the token sheet. Non-colour values are ignored."""
    found: dict[str, str] = {}
    for name, value in re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;", CSS.read_text()):
        found[name] = value
    return found


def channel(value: float) -> float:
    """One sRGB channel to linear light. The transfer function, not a gamma approximation."""
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    """Relative luminance, per WCAG 2.1."""
    raw = colour.lstrip("#")
    if len(raw) == 3:
        raw = "".join(pair * 2 for pair in raw)
    red, green, blue = (int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(foreground: str, background: str) -> float:
    """The ratio between two colours, from 1.0 (identical) to 21.0 (black on white)."""
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# -- the arithmetic itself ---------------------------------------------------------------------


def test_black_on_white_is_the_maximum() -> None:
    assert round(contrast("#000000", "#ffffff"), 1) == 21.0


def test_a_colour_against_itself_is_the_minimum() -> None:
    assert contrast("#123456", "#123456") == 1.0


def test_shorthand_hex_is_understood() -> None:
    assert contrast("#fff", "#000") == contrast("#ffffff", "#000000")


def test_the_ratio_does_not_depend_on_which_way_round_it_is_asked() -> None:
    assert contrast("#e9e6df", "#0a0c0d") == contrast("#0a0c0d", "#e9e6df")


# -- the tokens this project actually ships ----------------------------------------------------


def test_the_token_sheet_is_where_it_is_expected() -> None:
    assert CSS.is_file()
    assert tokens(), "no colour tokens were found; the sheet's shape must have changed"


@pytest.mark.parametrize(
    ("foreground", "background"),
    [
        ("--text-primary", "--surface-base"),
        ("--text-primary", "--surface-panel"),
        ("--text-transcript", "--surface-document"),
        ("--text-secondary", "--surface-panel"),
        ("--text-secondary", "--surface-control"),
    ],
)
def test_body_text_meets_the_aa_threshold(foreground, background) -> None:
    """4.5:1. `--text-transcript` on `--surface-document` is the one that matters most — it is read
    for two hours at a stretch."""
    palette = tokens()
    ratio = contrast(palette[foreground], palette[background])

    assert ratio >= AA_TEXT, f"{foreground} on {background} is {ratio:.2f}:1, below {AA_TEXT}"


@pytest.mark.parametrize(
    ("foreground", "background"),
    [
        ("--text-muted", "--surface-base"),
        ("--text-muted", "--surface-panel"),
    ],
)
def test_dimmed_text_meets_at_least_the_large_text_threshold(foreground, background) -> None:
    """3.0:1. Muted text is timestamps and counts — small, but never the only way to know
    something, so the large-text floor is the honest one to hold it to."""
    palette = tokens()
    ratio = contrast(palette[foreground], palette[background])

    assert ratio >= AA_LARGE, f"{foreground} on {background} is {ratio:.2f}:1, below {AA_LARGE}"


def test_the_transcript_is_the_most_readable_pairing_on_the_page() -> None:
    """A deliberate ranking, not an accident: whatever else is adjusted, the text someone reads for
    two hours should not end up dimmer than the chrome around it."""
    palette = tokens()
    transcript = contrast(palette["--text-transcript"], palette["--surface-document"])
    secondary = contrast(palette["--text-secondary"], palette["--surface-panel"])

    assert transcript > secondary
