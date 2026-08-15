"""The capture-mode vocabulary is defined twice and must stay identical (D-020).

``web/backend/app/services/session/modes.py`` is canonical;
``web/frontend/static/js/core/modes.js`` mirrors it so the browser can import it without a build
step. Two hand-maintained copies of the same vocabulary drift, and the way they drift is silent: a
mode renamed on one side leaves the other sending a string nobody handles, and the interface shows a
confidently wrong state rather than an error.

This test reads the JavaScript as text and compares it to the Python. Parsing with a regular
expression rather than a JavaScript engine is deliberate — it keeps the suite free of a Node
toolchain, which Decision D-011 removed on purpose. The cost is that `modes.js` must stay simple
enough to read this way, which its own header says.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.paths import STATIC_DIR
from app.services.session import modes

MODES_JS = STATIC_DIR / "js" / "core" / "modes.js"


@pytest.fixture(scope="module")
def js() -> str:
    assert MODES_JS.is_file(), f"The mirrored vocabulary is missing: {MODES_JS}"
    return MODES_JS.read_text(encoding="utf-8")


def _string_constants(source: str) -> dict[str, str]:
    """``export const NAME = "value";`` → ``{"NAME": "value"}``."""
    return dict(re.findall(r'export const (\w+) = "([^"]*)";', source))


def _string_array(source: str, name: str) -> list[str]:
    """The identifiers or literals inside ``export const NAME = Object.freeze([...]);``."""
    match = re.search(rf"export const {name} = Object\.freeze\(\[(.*?)\]\)", source, re.DOTALL)
    assert match, f"{name} is missing from modes.js"
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def _resolve(items: list[str], constants: dict[str, str]) -> list[str]:
    """Turn a list that may name constants into the values those constants hold."""
    return [constants.get(item, item.strip('"')) for item in items]


def _frozen_map(source: str, name: str) -> dict[str, list[str]]:
    """``export const NAME = Object.freeze({ key: Object.freeze([...]), ... });``"""
    match = re.search(rf"export const {name} = Object\.freeze\(\{{(.*?)\n\}}\);", source, re.DOTALL)
    assert match, f"{name} is missing from modes.js"
    entries = re.findall(r"(\w+): Object\.freeze\(\[(.*?)\]\)", match.group(1), re.DOTALL)
    return {
        key: [item.strip() for item in body.split(",") if item.strip()] for key, body in entries
    }


# -- the vocabularies themselves ---------------------------------------------------------


def test_every_mode_constant_matches(js: str) -> None:
    constants = _string_constants(js)
    expected = {"LIVE": modes.LIVE, "RECORDED": modes.RECORDED, "WINDOW": modes.WINDOW}
    for name, value in expected.items():
        assert constants.get(name) == value, f"{name} differs between Python and JavaScript"


def test_every_run_state_constant_matches(js: str) -> None:
    constants = _string_constants(js)
    expected = {
        "IDLE": modes.IDLE,
        "ARMING": modes.ARMING,
        "RECORDING": modes.RECORDING,
        "STOPPING": modes.STOPPING,
        "PROCESSING": modes.PROCESSING,
        "ERROR": modes.ERROR,
    }
    for name, value in expected.items():
        assert constants.get(name) == value, f"{name} differs between Python and JavaScript"


def test_mode_list_matches_in_order(js: str) -> None:
    constants = _string_constants(js)
    assert _resolve(_string_array(js, "CAPTURE_MODES"), constants) == list(modes.CAPTURE_MODES)


def test_state_list_matches_in_order(js: str) -> None:
    constants = _string_constants(js)
    assert _resolve(_string_array(js, "RECORD_STATES"), constants) == list(modes.RECORD_STATES)


def test_busy_states_match(js: str) -> None:
    constants = _string_constants(js)
    assert set(_resolve(_string_array(js, "BUSY_STATES"), constants)) == set(modes.BUSY_STATES)


# -- the mode → states map, which is where a mistake is most expensive -------------------


def test_mode_states_map_matches_edge_for_edge(js: str) -> None:
    constants = _string_constants(js)
    mirrored = {
        mode: _resolve(states, constants) for mode, states in _frozen_map(js, "MODE_STATES").items()
    }
    assert mirrored == {mode: list(states) for mode, states in modes.MODE_STATES.items()}


def test_mode_requirements_match(js: str) -> None:
    mirrored = {
        mode: [item.strip('"') for item in items]
        for mode, items in _frozen_map(js, "MODE_REQUIREMENTS").items()
    }
    assert mirrored == {mode: list(reqs) for mode, reqs in modes.MODE_REQUIREMENTS.items()}


# -- internal consistency, which catches a wrong edit on either side ---------------------


def test_every_mode_has_states_and_requirements() -> None:
    for mode in modes.CAPTURE_MODES:
        assert modes.MODE_STATES.get(mode), f"{mode} has no run states"
        assert modes.MODE_REQUIREMENTS.get(mode), f"{mode} has no requirements entry"


def test_every_mode_can_be_idle_and_can_fail() -> None:
    # Without these two every mode has a state it can enter and never leave.
    for mode, states in modes.MODE_STATES.items():
        assert modes.IDLE in states, f"{mode} cannot return to idle"
        assert modes.ERROR in states, f"{mode} has no way to report a failure"


def test_no_mode_declares_a_state_outside_the_vocabulary() -> None:
    for mode, states in modes.MODE_STATES.items():
        unknown = set(states) - set(modes.RECORD_STATES)
        assert not unknown, f"{mode} declares unknown states: {sorted(unknown)}"


def test_live_does_not_process_and_window_does() -> None:
    # The distinction the whole three-mode design rests on: live has nothing left to do when
    # capture stops, and the other two do.
    assert modes.PROCESSING not in modes.MODE_STATES[modes.LIVE]
    assert modes.PROCESSING in modes.MODE_STATES[modes.RECORDED]
    assert modes.PROCESSING in modes.MODE_STATES[modes.WINDOW]


def test_only_window_arms() -> None:
    # Arming exists to collect options and wait for the desktop's picker. Only window capture has
    # either, and offering an arming step for the others would be a dialog with nothing in it.
    assert modes.ARMING in modes.MODE_STATES[modes.WINDOW]
    assert modes.ARMING not in modes.MODE_STATES[modes.LIVE]
    assert modes.ARMING not in modes.MODE_STATES[modes.RECORDED]


def test_helpers_agree_with_the_tables() -> None:
    assert modes.states_for(modes.LIVE) == modes.MODE_STATES[modes.LIVE]
    assert modes.is_valid_mode(modes.WINDOW)
    assert not modes.is_valid_mode("screen")
    assert modes.is_busy(modes.RECORDING)
    assert not modes.is_busy(modes.IDLE)

    with pytest.raises(ValueError, match="Unknown capture mode"):
        modes.states_for("screen")


def test_the_mirror_is_where_the_documentation_says_it_is() -> None:
    # A rename that moved the file would make every parsing test above skip silently if the
    # fixture merely returned an empty string, so the location is asserted on its own.
    assert MODES_JS == Path(STATIC_DIR, "js", "core", "modes.js")
    assert MODES_JS.is_file()
