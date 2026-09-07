"""The product name, and the promise that no legacy identifier is also a current one.

These are cheap assertions about constants, which is exactly why they are worth having. The failure
they exist to catch is a rename that lands halfway: a ``LEGACY_*`` value silently equal to its
replacement reads as "migration handled" everywhere it is used, while migrating nothing at all.
"""

from __future__ import annotations

import pytest

from app import branding


def test_the_title_is_the_name_and_the_tagline():
    assert branding.APP_NAME == "Loud Radish"
    assert branding.APP_TITLE.startswith(branding.APP_NAME)
    assert branding.APP_TITLE.endswith(branding.APP_TAGLINE)


def test_the_slug_is_the_name_lowercased_and_hyphenated():
    assert branding.APP_SLUG == branding.APP_NAME.lower().replace(" ", "-")


#: Every constant that names state existing outside this repository, paired with the value an
#: earlier build wrote. A pair here is a promise that the migration path is real.
LEGACY_PAIRS = [
    ("CONFIG_FILENAME", "LEGACY_CONFIG_FILENAME"),
    ("KEYRING_SERVICE", "LEGACY_KEYRING_SERVICE"),
    ("SHORTCUT_COMPONENT", "LEGACY_SHORTCUT_COMPONENT"),
    ("SERVICE_UNIT", "LEGACY_SERVICE_UNIT"),
    ("DESKTOP_ENTRY", "LEGACY_DESKTOP_ENTRY"),
    ("ENV_PREFIX", "LEGACY_ENV_PREFIX"),
]


@pytest.mark.parametrize(("current", "legacy"), LEGACY_PAIRS)
def test_a_legacy_identifier_is_never_the_current_one(current: str, legacy: str):
    assert getattr(branding, current) != getattr(branding, legacy)


def test_the_legacy_tap_prefixes_do_not_include_the_current_one():
    """The sweep removes sinks matching a legacy prefix. Listing the current one would make it
    delete the tap belonging to the running session."""
    assert branding.TAP_SINK_PREFIX not in branding.LEGACY_TAP_SINK_PREFIXES
    assert branding.LEGACY_TAP_SINK_PREFIXES


def test_environment_variables_are_built_from_the_prefixes():
    assert branding.env_var("CONFIG_PATH") == "LOUD_RADISH_CONFIG_PATH"
    assert branding.legacy_env_var("CONFIG_PATH") == "TRANSCRIBER_CONFIG_PATH"


def test_the_brand_palette_is_five_hex_colours():
    assert set(branding.BRAND_COLORS) == {"radish", "leaf", "cream", "blush", "ink"}
    for name, value in branding.BRAND_COLORS.items():
        assert value.startswith("#") and len(value) == 7, name
