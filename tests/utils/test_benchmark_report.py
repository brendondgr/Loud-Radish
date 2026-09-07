"""The benchmark's report and its agreement scoring (D-053).

The measuring itself needs a GPU, a model and a minute; none of that belongs in the suite. What is
tested here is the arithmetic around it — and specifically the mistake that made the first run's
output useless: agreement was scored against "the largest model that ran", and on this machine the
largest model on the GPU returned 99 words for a 54-word clip, so every row was compared with a
hallucination and the whole column read 39%.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from benchmark_asr import Result, report, score_agreement  # noqa: E402


def _result(model: str, words: str, seconds: float = 1.0, audio: float = 25.0) -> Result:
    entry = Result(model=model, device="cpu", precision="int8")
    entry.text = words
    entry.words = len(words.split())
    entry.transcribe_seconds = seconds
    entry.audio_seconds = audio
    return entry


# -- speed -------------------------------------------------------------------------------------


def test_the_realtime_factor_is_audio_over_wall_clock() -> None:
    entry = _result("base", "a b c", seconds=2.0, audio=50.0)

    assert entry.realtime_factor == 25.0


def test_a_result_that_never_ran_reports_no_speed_rather_than_dividing_by_zero() -> None:
    assert Result(model="base", device="cuda", precision="int8").realtime_factor == 0.0


# -- agreement ---------------------------------------------------------------------------------


def test_agreement_is_scored_against_the_median_length_transcript() -> None:
    """**The fix.** A hallucinating outlier must not become everyone's yardstick."""
    good_a = _result("small", "the central claim is that two operators commute")
    good_b = _result("base", "the central claim is that two operators commute")
    rambling = _result("large", " ".join(["the central claim is that two operators commute"] * 4))

    score_agreement([good_a, good_b, rambling])

    assert good_a.agreement == 1.0
    assert good_b.agreement == 1.0
    assert rambling.agreement < 0.6, "the outlier should stand out, not set the standard"


def test_an_empty_transcript_is_not_used_as_the_reference() -> None:
    """A GPU combination that returns zero words is a broken row, not a strict grader."""
    empty = _result("base", "")
    real = _result("small", "the central claim is that two operators commute")

    score_agreement([empty, real])

    assert real.agreement == 1.0


def test_a_failed_result_is_left_alone() -> None:
    failed = Result(model="small", device="cuda", precision="int8", error="GPU refused it")

    score_agreement([failed, _result("base", "some words here")])

    assert failed.agreement == 0.0


def test_nothing_to_compare_is_not_an_error() -> None:
    score_agreement([])
    score_agreement([Result(model="x", device="cpu", precision="int8", error="nope")])


# -- the report --------------------------------------------------------------------------------


def test_a_failed_combination_is_shown_with_its_reason_rather_than_omitted() -> None:
    """**A row that vanished would read as a combination nobody tried.** The whole point of this
    table is to say which options this machine actually has."""
    failed = Result(
        model="small", device="cuda", precision="int8", error="GPU memory fault on load"
    )

    text = report([failed], "clip.wav")

    assert "small" in text
    assert "unavailable" in text
    assert "GPU memory fault" in text


def test_the_table_names_what_it_measured_over() -> None:
    text = report([_result("base", "words")], "seminar.wav")

    assert "seminar.wav" in text


def test_the_units_are_stated_rather_than_assumed() -> None:
    """ "40x" means nothing without saying 40 times what."""
    text = report([_result("base", "words")], "clip.wav")

    assert "seconds of audio" in text
    assert "not with the truth" in text


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_a_zero_or_negative_duration_does_not_crash_the_table(bad) -> None:
    entry = _result("base", "words", seconds=bad)

    assert "base" in report([entry], "clip.wav")
