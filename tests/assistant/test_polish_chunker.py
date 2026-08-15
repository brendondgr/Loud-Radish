"""The chunk planner: when a minute of transcript is ready to be polished.

The behaviour under test is the requirement itself — accumulate about a minute, then cut at a
natural break rather than at a hard boundary — plus the two escapes that keep it from stalling.
"""

from __future__ import annotations

from app.config.schema import PolishConfig
from app.services.polish.chunker import MIN_CHUNK_SECONDS, decide_cut


def config(**overrides: object) -> PolishConfig:
    """A polish config with the defaults the feature ships with."""
    return PolishConfig(**overrides)  # type: ignore[arg-type]


class TestAccumulating:
    def test_nothing_committed_yet_is_not_a_cut(self) -> None:
        decision = decide_cut(
            cursor=60.0, last_committed_end=60.0, silence_seconds=9.0, config=config()
        )
        assert not decision.should_cut

    def test_half_a_minute_keeps_accumulating_even_in_silence(self) -> None:
        """The pause is the trigger, but only once there is a minute's worth to trigger on."""
        decision = decide_cut(
            cursor=0.0, last_committed_end=30.0, silence_seconds=9.0, config=config()
        )
        assert not decision.should_cut
        assert "accumulated" in decision.reason


class TestTheNaturalBreak:
    def test_a_full_minute_still_mid_sentence_waits(self) -> None:
        decision = decide_cut(
            cursor=0.0, last_committed_end=61.0, silence_seconds=0.0, config=config()
        )
        assert not decision.should_cut
        assert decision.reason == "waiting for a pause in speech"

    def test_a_brief_breath_is_not_a_break(self) -> None:
        decision = decide_cut(
            cursor=0.0, last_committed_end=61.0, silence_seconds=0.9, config=config()
        )
        assert not decision.should_cut

    def test_two_seconds_of_silence_cuts_at_the_last_committed_word(self) -> None:
        decision = decide_cut(
            cursor=0.0, last_committed_end=64.5, silence_seconds=2.1, config=config()
        )
        assert decision.cut_at == 64.5

    def test_the_cut_never_runs_past_committed_transcript(self) -> None:
        """Anything past it is still in the hypothesis tail and would be dropped silently."""
        decision = decide_cut(
            cursor=0.0, last_committed_end=70.0, silence_seconds=30.0, config=config()
        )
        assert decision.cut_at == 70.0


class TestTheCeiling:
    def test_a_speaker_who_never_pauses_still_gets_cut(self) -> None:
        decision = decide_cut(
            cursor=0.0, last_committed_end=150.0, silence_seconds=0.0, config=config()
        )
        assert decision.cut_at == 150.0
        assert "ceiling" in decision.reason

    def test_the_ceiling_applies_with_the_vad_disabled(self) -> None:
        """A disabled VAD reports zero silence forever, so this is the only trigger left."""
        settings = config(chunk_seconds=60.0, max_chunk_seconds=90.0)
        assert not decide_cut(
            cursor=0.0, last_committed_end=80.0, silence_seconds=0.0, config=settings
        ).should_cut
        assert (
            decide_cut(
                cursor=0.0, last_committed_end=95.0, silence_seconds=0.0, config=settings
            ).cut_at
            == 95.0
        )


class TestTheFinalPass:
    def test_a_talk_that_ends_mid_chunk_still_polishes_its_conclusion(self) -> None:
        decision = decide_cut(
            cursor=600.0, last_committed_end=640.0, silence_seconds=0.0, config=config(), final=True
        )
        assert decision.cut_at == 640.0
        assert decision.reason == "final pass"

    def test_a_two_second_tail_is_not_worth_a_model_call(self) -> None:
        decision = decide_cut(
            cursor=600.0,
            last_committed_end=600.0 + MIN_CHUNK_SECONDS - 1,
            silence_seconds=0.0,
            config=config(),
            final=True,
        )
        assert not decision.should_cut


def test_settings_are_read_fresh_on_every_decision() -> None:
    """A change in settings must apply on the next tick, not at the next restart."""
    early = config(chunk_seconds=20.0)
    assert decide_cut(
        cursor=0.0, last_committed_end=25.0, silence_seconds=3.0, config=early
    ).should_cut
    assert not decide_cut(
        cursor=0.0, last_committed_end=25.0, silence_seconds=3.0, config=config()
    ).should_cut
