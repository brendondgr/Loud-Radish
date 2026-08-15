"""What the application does when something goes wrong (BE §15).

One rule governs every case here, and it is the reason this module is separate from the happy-path
tests:

    **Transcription is the critical path.** A language-model failure is an inconvenience; a lost
    transcript is a ruined seminar.

So each test asserts two things about a failure — that the message names a remedy rather than
merely reporting a fault, and that the ``transcription_continues`` flag is honest about whether the
talk is still being recorded. A failure marked critical when recording continues teaches the user
to ignore the tier that matters.
"""

from __future__ import annotations

import pytest
from app.services.session import degradation
from app.services.streaming.guards import Severity


def test_every_failure_names_something_to_do() -> None:
    """ "Connection failed" leaves the user stuck mid-talk. Each message must be actionable."""
    failures = [
        degradation.device_lost("Samson GoMic"),
        degradation.model_load_failed("large-v3", "Out of memory loading the model"),
        degradation.out_of_memory("large-v3", "cuda"),
        degradation.falling_behind(0.6, "medium"),
        degradation.dropped_audio(3, "small"),
        degradation.disk_full("No space left on device"),
        degradation.llm_unavailable("No server responded at http://localhost:11434/v1"),
    ]

    for failure in failures:
        assert failure.message.strip(), failure.code
        # Either a one-click fix, a settings tab, or an instruction inside the message itself.
        actionable = (
            failure.remedy
            or failure.opens_settings
            or any(
                word in failure.message.lower()
                for word in ("check", "free", "try", "switch", "choose", "install", "lower")
            )
        )
        assert actionable, f"{failure.code} gives the user nothing to do: {failure.message}"


def test_a_labelled_button_always_has_something_behind_it() -> None:
    """A remedy label with neither a change nor a tab renders as text with nothing to click."""
    for failure in (
        degradation.device_lost("Mic"),
        degradation.out_of_memory("small", "cuda"),
        degradation.falling_behind(0.5, "small"),
        degradation.llm_unavailable("gone"),
        degradation.model_load_failed("tiny", "broken"),
    ):
        if failure.remedy_label:
            assert failure.remedy or failure.opens_settings, failure.code


# -- what stops recording, and what does not ----------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        degradation.device_lost("Mic"),
        degradation.model_load_failed("small", "broken"),
        degradation.out_of_memory("small", "cuda"),
    ],
)
def test_audio_and_model_failures_stop_transcription(failure) -> None:
    assert failure.transcription_continues is False
    assert failure.severity is Severity.CRITICAL


@pytest.mark.parametrize(
    "failure",
    [
        degradation.dropped_audio(2, "small"),
        degradation.llm_unavailable("no server"),
    ],
)
def test_everything_else_lets_the_talk_carry_on(failure) -> None:
    assert failure.transcription_continues is True


def test_a_full_disk_keeps_recording_in_memory() -> None:
    """Stopping would guarantee losing what a freed-up disk might still save."""
    failure = degradation.disk_full("No space left on device")

    assert failure.transcription_continues is True
    assert "continues" in failure.message


def test_an_assistant_failure_is_never_critical() -> None:
    """Critical is reserved for what stops the transcript. The assistant is a tool."""
    failure = degradation.llm_unavailable("No server responded")

    assert failure.severity is Severity.WARNING
    assert failure.opens_settings == "llm"


# -- the fallback ladder --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("large-v3", "medium"),
        ("medium", "small"),
        ("small", "base"),
        ("base", "tiny"),
        ("tiny", None),
    ],
)
def test_each_model_knows_the_next_one_down(model: str, expected: str | None) -> None:
    assert degradation.smaller_model(model) == expected


def test_running_out_of_memory_on_the_smallest_model_falls_back_to_the_cpu() -> None:
    """There is no smaller model, so the remaining move is a different device."""
    failure = degradation.out_of_memory("tiny", "cuda")

    assert failure.remedy == {"asr.device": "cpu"}
    assert "CPU" in failure.remedy_label


def test_falling_behind_offers_the_next_model_down() -> None:
    failure = degradation.falling_behind(0.6, "medium")

    assert failure.remedy == {"asr.model": "small"}
    assert "0.6" in failure.message


def test_falling_behind_on_the_smallest_model_says_what_else_to_try() -> None:
    failure = degradation.falling_behind(0.4, "tiny")

    assert failure.remedy is None
    assert "faster compute device" in failure.message


def test_a_lost_device_says_the_transcript_is_safe() -> None:
    """Nothing committed has become untrue, and the user's first fear is that it has."""
    failure = degradation.device_lost("Samson GoMic")

    assert "safe" in failure.message
    assert "Samson GoMic" in failure.message
    assert failure.opens_settings == "audio"


def test_dropped_audio_admits_words_are_missing_and_says_how_to_stop_it() -> None:
    """The transcript has a hole in it, and the rest of the talk is about to get the same."""
    failure = degradation.dropped_audio(4, "medium")

    assert "missing" in failure.message
    assert "4" in failure.message
    assert failure.remedy == {"asr.model": "small"}


def test_dropped_audio_on_the_smallest_model_still_suggests_something() -> None:
    failure = degradation.dropped_audio(4, "tiny")

    assert failure.remedy is None
    assert failure.opens_settings == "asr"
    assert "faster compute device" in failure.message


# -- the event shape ---------------------------------------------------------------------------


def test_the_event_carries_everything_the_banner_needs() -> None:
    event = degradation.out_of_memory("medium", "cuda").as_event()

    assert set(event) == {
        "code",
        "message",
        "severity",
        "remedy",
        "remedy_label",
        "opens_settings",
        "transcription_continues",
    }
    assert isinstance(event["severity"], str)
