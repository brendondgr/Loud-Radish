"""The six guards (BE §7.6), and repetition detection specifically (BE §19.2)."""

from __future__ import annotations

import pytest
from app.config.schema import StreamingConfig
from app.services.asr.contract import WordToken
from app.services.streaming import GuardAction, Severity, StreamGuards, find_repetition


def words(text: str) -> list[WordToken]:
    return [
        WordToken(text=token, start=i * 0.3, end=(i + 1) * 0.3)
        for i, token in enumerate(text.split())
    ]


def guards(**overrides: object) -> StreamGuards:
    settings: dict = {
        "min_buffer_s": 1.0,
        "max_buffer_s": 25.0,
        "commit_timeout_s": 15.0,
        "repetition_limit": 3,
        **overrides,
    }
    return StreamGuards(StreamingConfig(**settings))


class TestMinimumBuffer:
    def test_a_short_buffer_skips_inference(self) -> None:
        """Wasted passes on fragments cost real time and produce nothing."""
        decision = guards().before_inference(
            buffer_seconds=0.4, audio_time=5.0, speaking=True, has_hypothesis=False
        )
        assert decision.action is GuardAction.SKIP
        assert decision.guard == "minimum-buffer"

    def test_a_long_enough_buffer_proceeds(self) -> None:
        decision = guards().before_inference(
            buffer_seconds=3.0, audio_time=5.0, speaking=True, has_hypothesis=False
        )
        assert decision.should_infer


class TestMaximumBuffer:
    def test_an_oversized_buffer_forces_a_commit(self) -> None:
        """Exceeding the model's window silently truncates output — the worst kind of failure."""
        decision = guards().before_inference(
            buffer_seconds=26.0, audio_time=30.0, speaking=True, has_hypothesis=True
        )
        assert decision.action is GuardAction.FORCE_COMMIT
        assert decision.guard == "maximum-buffer"

    def test_it_outranks_the_silence_gate(self) -> None:
        """An oversized buffer must be dealt with even if the speaker has stopped."""
        decision = guards().before_inference(
            buffer_seconds=26.0, audio_time=30.0, speaking=False, has_hypothesis=True
        )
        assert decision.guard == "maximum-buffer"

    def test_the_reason_names_the_actual_numbers(self) -> None:
        decision = guards(max_buffer_s=20.0).before_inference(
            buffer_seconds=26.0, audio_time=30.0, speaking=True, has_hypothesis=True
        )
        assert "26.0" in decision.reason and "20" in decision.reason


class TestSilenceGate:
    def test_silence_skips_inference(self) -> None:
        """Autoregressive models invent text when fed silence; this is the main mitigation."""
        decision = guards().before_inference(
            buffer_seconds=5.0, audio_time=10.0, speaking=False, has_hypothesis=False
        )
        assert decision.action is GuardAction.SKIP
        assert decision.guard == "silence-gate"

    def test_silence_commits_a_pending_hypothesis(self) -> None:
        """The speaker stopped; waiting for agreement on audio that is not coming is pointless."""
        decision = guards().before_inference(
            buffer_seconds=5.0, audio_time=10.0, speaking=False, has_hypothesis=True
        )
        assert decision.action is GuardAction.SKIP_AND_COMMIT
        assert decision.should_commit

    def test_it_runs_before_the_minimum_buffer_check(self) -> None:
        """Otherwise a short buffer at the end of speech never commits its tail."""
        decision = guards().before_inference(
            buffer_seconds=0.2, audio_time=10.0, speaking=False, has_hypothesis=True
        )
        assert decision.guard == "silence-gate"


class TestCommitTimeout:
    def test_continuous_speech_eventually_forces_a_commit(self) -> None:
        """A speaker talking for two minutes must not produce two minutes of silence in the UI."""
        guard = guards(commit_timeout_s=15.0)
        guard.note_commit(0.0)
        decision = guard.before_inference(
            buffer_seconds=10.0, audio_time=16.0, speaking=True, has_hypothesis=True
        )
        assert decision.action is GuardAction.FORCE_COMMIT
        assert decision.guard == "commit-timeout"

    def test_it_does_not_fire_before_the_timeout(self) -> None:
        guard = guards(commit_timeout_s=15.0)
        guard.note_commit(0.0)
        decision = guard.before_inference(
            buffer_seconds=10.0, audio_time=9.0, speaking=True, has_hypothesis=True
        )
        assert decision.should_infer

    def test_a_commit_resets_the_timer(self) -> None:
        guard = guards(commit_timeout_s=15.0)
        guard.note_commit(0.0)
        guard.note_commit(20.0)
        decision = guard.before_inference(
            buffer_seconds=10.0, audio_time=25.0, speaking=True, has_hypothesis=True
        )
        assert decision.should_infer

    def test_it_does_not_fire_with_nothing_pending(self) -> None:
        """Forcing a commit of nothing would emit an empty segment."""
        guard = guards(commit_timeout_s=15.0)
        guard.note_commit(0.0)
        decision = guard.before_inference(
            buffer_seconds=10.0, audio_time=99.0, speaking=True, has_hypothesis=False
        )
        assert decision.should_infer


class TestRepetitionDetection:
    def test_a_repeated_unigram_is_caught(self) -> None:
        keep = find_repetition(words("and so and and and and and"), limit=3)
        assert keep is not None

    def test_a_repeated_phrase_is_caught(self) -> None:
        """Whisper's characteristic failure: a phrase looping indefinitely."""
        keep = find_repetition(words("the point is and so and so and so and so"), limit=3)
        assert keep is not None
        assert " ".join(w.text for w in words("the point is and so and so and so and so")[:keep])

    def test_the_phrase_is_kept_once(self) -> None:
        """It was probably said, just not this many times."""
        tokens = words("thank you thank you thank you thank you thank you")
        keep = find_repetition(tokens, limit=3)
        assert keep is not None
        assert [w.text for w in tokens[:keep]] == ["thank", "you"]

    def test_ordinary_speech_is_not_flagged(self) -> None:
        text = "the central claim here is that these operators commute only on a dense subspace"
        assert find_repetition(words(text), limit=3) is None

    def test_a_phrase_repeated_within_the_limit_is_allowed(self) -> None:
        """People do repeat themselves for emphasis."""
        assert find_repetition(words("no no no it is not"), limit=3) is None

    def test_short_output_is_never_flagged(self) -> None:
        assert find_repetition(words("a b"), limit=3) is None

    def test_punctuation_differences_do_not_hide_a_loop(self) -> None:
        tokens = words("and so. and so, and so! and so and so")
        assert find_repetition(tokens, limit=3) is not None

    def test_the_limit_is_configurable(self) -> None:
        tokens = words("yes yes yes yes yes yes")
        assert find_repetition(tokens, limit=10) is None
        assert find_repetition(tokens, limit=2) is not None

    def test_the_guard_wires_the_configured_limit_through(self) -> None:
        tokens = words("hmm hmm hmm hmm hmm hmm hmm")
        assert guards(repetition_limit=3).check_repetition(tokens) is not None


class TestBackpressure:
    def test_a_real_time_factor_below_one_is_critical(self) -> None:
        """C3: below 1 the system will never catch up on its own."""
        warning = guards().check_backpressure(real_time_factor=0.8, queue_fill=0.2)
        assert warning is not None
        assert warning.severity is Severity.CRITICAL
        assert warning.code == "falling-behind"

    def test_the_message_names_a_specific_remedy(self) -> None:
        """ "Falling behind" with no suggested action leaves the user stuck mid-talk."""
        warning = guards().check_backpressure(real_time_factor=0.8, queue_fill=0.2)
        assert warning is not None
        assert "smaller model" in warning.message

    def test_a_healthy_factor_produces_no_warning(self) -> None:
        assert guards().check_backpressure(real_time_factor=1.8, queue_fill=0.1) is None

    def test_a_saturated_queue_warns_before_the_factor_drops(self) -> None:
        warning = guards().check_backpressure(real_time_factor=1.4, queue_fill=0.95)
        assert warning is not None
        assert warning.severity is Severity.WARNING

    def test_recovery_is_announced_once(self) -> None:
        """Otherwise the banner stays up after the problem has gone."""
        guard = guards()
        guard.check_backpressure(real_time_factor=0.7, queue_fill=0.5)

        recovered = guard.check_backpressure(real_time_factor=2.0, queue_fill=0.1)
        assert recovered is not None and recovered.severity is Severity.INFO
        assert guard.check_backpressure(real_time_factor=2.0, queue_fill=0.1) is None

    def test_a_zero_factor_before_any_inference_is_not_a_failure(self) -> None:
        assert guards().check_backpressure(real_time_factor=0.0, queue_fill=0.0) is None


class TestGuardLifecycle:
    def test_reset_clears_the_commit_timer(self) -> None:
        guard = guards(commit_timeout_s=15.0)
        guard.note_commit(100.0)
        guard.reset()
        decision = guard.before_inference(
            buffer_seconds=5.0, audio_time=16.0, speaking=True, has_hypothesis=True
        )
        assert decision.guard == "commit-timeout"

    def test_config_updates_apply_immediately(self) -> None:
        guard = guards(min_buffer_s=1.0)
        guard.update_config(StreamingConfig(min_buffer_s=5.0))
        decision = guard.before_inference(
            buffer_seconds=3.0, audio_time=10.0, speaking=True, has_hypothesis=False
        )
        assert decision.guard == "minimum-buffer"

    def test_a_proceed_decision_names_no_guard(self) -> None:
        decision = guards().before_inference(
            buffer_seconds=5.0, audio_time=5.0, speaking=True, has_hypothesis=False
        )
        assert decision.guard == ""
        assert not decision.should_commit


@pytest.mark.parametrize("severity", list(Severity))
def test_every_severity_matches_the_transport_contract(severity: Severity) -> None:
    assert str(severity) in {"info", "warning", "critical"}
