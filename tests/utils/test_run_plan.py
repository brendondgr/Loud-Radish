"""The record sheet's answers, settled into one mode and one run (D-075).

The table in `docs/design-system.md` § Capture Modes, as code. Pure functions, so no device, no
model and no session are needed to check every row of it.
"""

from __future__ import annotations

import pytest
from app.services.session import CaptureOptions, modes
from app.services.session.plan import OptionsConflict, plan_run, preset, resolve


def _options(source: str, *, live: bool, post: bool, video: bool = False) -> CaptureOptions:
    return CaptureOptions(
        source=source, live_transcription=live, post_transcription=post, video=video
    )


@pytest.mark.parametrize(
    ("source", "live", "post", "expected"),
    [
        ("microphone", True, False, modes.LIVE),
        ("microphone", False, True, modes.RECORDED),
        ("microphone", True, True, modes.RECORDED),
        ("system", True, False, modes.LIVE),
        ("system", True, True, modes.RECORDED),
        ("window", True, False, modes.WINDOW),
        ("window", False, False, modes.WINDOW),
    ],
)
def test_every_row_of_the_table_derives_its_mode(source, live, post, expected) -> None:
    mode, _ = resolve(None, _options(source, live=live, post=post, video=source == "window"))
    assert mode == expected


def test_nothing_at_all_is_the_original_behaviour() -> None:
    mode, options = resolve(None, None)
    assert mode == modes.LIVE
    assert options.source == "microphone"
    assert plan_run(options) == plan_run(preset(modes.LIVE))


@pytest.mark.parametrize("mode", list(modes.CAPTURE_MODES))
def test_a_mode_alone_gets_the_combination_its_tab_used_to_offer(mode: str) -> None:
    resolved, options = resolve(mode, None)
    assert resolved == mode
    # The preset must itself resolve to the same mode, or a shortcut would start one kind of
    # recording and report another.
    assert resolve(None, options)[0] == mode


def test_a_pre_sheet_window_request_still_records_a_window() -> None:
    """Old clients sent `mode=window` and three switches, with no source."""
    mode, options = resolve(modes.WINDOW, CaptureOptions(post_transcription=False))
    assert mode == modes.WINDOW
    assert options.source == "window"
    assert plan_run(options).video is True


def test_video_is_dropped_for_an_audio_source_rather_than_refused() -> None:
    _, options = resolve(None, _options("microphone", live=True, post=False, video=True))
    assert options.video is False
    assert plan_run(options).video is False


def test_both_passes_start_an_engine_and_a_sink() -> None:
    _, options = resolve(None, _options("microphone", live=True, post=True))
    plan = plan_run(options)
    assert plan.engine and plan.sink and not plan.video


def test_a_contradiction_is_refused_and_names_both_sides() -> None:
    with pytest.raises(OptionsConflict) as raised:
        resolve(modes.LIVE, _options("microphone", live=False, post=True))
    message = str(raised.value)
    assert "'live'" in message and "'recorded'" in message


def test_an_unknown_source_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown capture source"):
        modes.derive_mode("screen", post_transcription=False)


# -- what a run's sound is made of (D-081) --------------------------------------------------------


def _inputs(source: str, inputs=(), audio_source: str = "system", levels=None) -> CaptureOptions:
    return CaptureOptions(
        source=source,
        live_transcription=True,
        post_transcription=False,
        video=source == "window",
        audio_source=audio_source,
        audio_inputs=tuple(inputs),
        levels=dict(levels or {}),
    )


@pytest.mark.parametrize(
    ("source", "audio_source", "expected"),
    [
        ("microphone", "system", ("microphone",)),
        ("system", "system", ("system",)),
        ("window", "application", ("window",)),
        ("window", "system", ("system",)),
        ("window", "microphone", ("microphone",)),
        # A window preset leaves the choice to configuration, which the session reads.
        ("window", "", ()),
    ],
)
def test_unstated_inputs_are_what_the_source_always_meant(source, audio_source, expected) -> None:
    """Every request from before the mixer records exactly what it did."""
    _, options = resolve(None, _inputs(source, audio_source=audio_source))
    assert options.audio_inputs == expected


@pytest.mark.parametrize(
    ("source", "inputs"),
    [
        ("window", ("window", "microphone")),
        ("window", ("system", "microphone")),
        ("system", ("system", "microphone")),
        ("microphone", ("microphone",)),
    ],
)
def test_the_offered_combinations_are_accepted(source, inputs) -> None:
    _, options = resolve(None, _inputs(source, inputs, levels={inputs[0]: 40}))
    assert options.audio_inputs == inputs
    assert options.levels == {inputs[0]: 40}


@pytest.mark.parametrize(
    ("source", "inputs", "levels", "says"),
    [
        ("window", ("window", "system"), {}, "already part of"),
        ("microphone", ("window",), {}, "only be recorded with a window"),
        ("microphone", ("system",), {}, "has to include its sound"),
        ("system", ("microphone",), {}, "has to include its sound"),
        ("window", ("microphone", "microphone"), {}, "twice"),
        ("window", ("window", "microphone", "system"), {}, "At most"),
        ("window", ("window",), {"microphone": 50}, "not being recorded"),
        ("window", ("window",), {"window": 150}, "between 0 and 100"),
    ],
)
def test_combinations_that_cannot_be_recorded_are_refused(source, inputs, levels, says) -> None:
    with pytest.raises(OptionsConflict, match=says):
        resolve(None, _inputs(source, inputs, levels=levels))
