"""Which picture the tray shows, from the mode *and* the run state (docs/motion-spec.md).

The one property worth a file of its own: **live-mode recording and recorded-mode recording are the
same run state and different pictures.** The specification draws one as a travelling teal sine and
the other as a red grille pulsing as one body, and a map keyed on run state alone would collapse
them — losing the distinction the whole design is built around.
"""

from __future__ import annotations

import pytest
from app.companion import visual_states as vs
from app.services.session import modes


def test_the_two_recording_pictures_stay_distinct() -> None:
    """The finding that changed Plan 5: the indicator is a function of (mode, run state)."""
    live = vs.visual_for(modes.LIVE, modes.RECORDING)
    recorded = vs.visual_for(modes.RECORDED, modes.RECORDING)

    assert live.name != recorded.name
    assert live.hue != recorded.hue
    assert live.motion == "wave", "live transcription should be reading, not just listening"
    assert recorded.motion == "hold", "a recorded capture should be capturing, not reading"


def test_a_window_capture_reads_like_live_transcription() -> None:
    """It is doing the same thing: listening *and* writing."""
    assert vs.visual_for(modes.WINDOW, modes.RECORDING).name == vs.LIVE


@pytest.mark.parametrize("mode", list(modes.CAPTURE_MODES))
def test_every_mode_and_state_pair_maps_somewhere(mode: str) -> None:
    """No pair may fall through to nothing — a tray with no icon is worse than a wrong one."""
    for state in modes.states_for(mode):
        visual = vs.visual_for(mode, state)
        assert visual.name in vs.VISUALS
        assert visual.hue
        assert visual.period > 0


def test_idle_is_colourless() -> None:
    """The design's governing rule: no hue is permitted at rest."""
    assert vs.visual_for(modes.LIVE, modes.IDLE).hue == "#8A8F95"


def test_the_fault_state_is_the_one_exception_to_that() -> None:
    """Warm while doing nothing, because doing nothing is the problem."""
    fault = vs.visual_for(modes.LIVE, modes.ERROR)
    assert fault.name == vs.FAULT
    assert "oklch" in fault.hue


def test_processing_shows_the_read_head_whatever_the_mode() -> None:
    for mode in (modes.RECORDED, modes.WINDOW):
        assert vs.visual_for(mode, modes.PROCESSING).name == vs.TRANSCRIBING


def test_stopping_still_looks_like_recording() -> None:
    """It has not finished yet, and changing the picture would say it had."""
    assert vs.visual_for(modes.LIVE, modes.STOPPING).name == vs.LIVE


def test_arming_borrows_idle_rather_than_inventing_a_picture() -> None:
    """The specification has no entry for it; the front half of its Idle-to-Listening transition,
    held, is closer to the design than something made up."""
    assert vs.visual_for(modes.WINDOW, modes.ARMING).name == vs.IDLE


def test_a_background_rewrite_shows_only_when_nothing_louder_is_happening() -> None:
    """Rewriting runs *during* a session (D-018), so it has no run state of its own. An instrument
    that stopped looking like it was recording because a background pass started would be lying
    about the thing that matters most."""
    assert vs.visual_for(modes.LIVE, modes.IDLE, running_pass=True).name == vs.REWRITING
    assert vs.visual_for(modes.LIVE, modes.RECORDING, running_pass=True).name == vs.LIVE


def test_a_dead_server_has_its_own_entry() -> None:
    """Not in the specification — it has no state for "the thing behind the icon is not there"."""
    assert vs.SERVER_DOWN in vs.VISUALS


def test_every_visual_names_its_leds() -> None:
    """The redundant signal that keeps hue from being the only one."""
    for name, visual in vs.VISUALS.items():
        assert len(visual.leds) == 3, name
