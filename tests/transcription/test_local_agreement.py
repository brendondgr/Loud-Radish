"""LocalAgreement-*n* — the commit policy (BE §7.3).

BE §19.2 names longest-common-prefix logic as one of the four things that must have unit coverage.
Every case here is written as word lists rather than audio, so the rule is tested independently of
any model.
"""

from __future__ import annotations

import pytest
from app.services.asr.contract import WordToken
from app.services.streaming import LocalAgreement, longest_common_prefix, normalise


def words(text: str, start: float = 0.0, step: float = 0.4) -> list[WordToken]:
    """Build a word list with plausible increasing timestamps."""
    return [
        WordToken(text=token, start=start + i * step, end=start + (i + 1) * step)
        for i, token in enumerate(text.split())
    ]


def texts(tokens: list[WordToken]) -> list[str]:
    return [token.text for token in tokens]


class TestNormalise:
    def test_case_is_ignored(self) -> None:
        assert normalise("Matrix") == normalise("matrix")

    def test_trailing_punctuation_is_ignored(self) -> None:
        """A comma added in the second pass is not the model changing its mind."""
        assert normalise("matrix,") == normalise("matrix")
        assert normalise("real.") == normalise("real")

    def test_a_word_made_entirely_of_punctuation_survives(self) -> None:
        assert normalise("—") == "—"


class TestLongestCommonPrefix:
    def test_the_worked_example_from_the_architecture(self) -> None:
        pass_n = words("of the matrix are always")
        pass_n1 = words("of the matrix are all real")
        assert longest_common_prefix([pass_n, pass_n1]) == 4

    def test_complete_agreement_returns_the_shorter_length(self) -> None:
        assert longest_common_prefix([words("a b c"), words("a b c d")]) == 3

    def test_divergence_at_the_first_word_returns_zero(self) -> None:
        assert longest_common_prefix([words("alpha b"), words("beta b")]) == 0

    def test_an_empty_list_contributes_nothing(self) -> None:
        assert longest_common_prefix([words("a b"), []]) == 0

    def test_no_lists_at_all_is_zero(self) -> None:
        assert longest_common_prefix([]) == 0

    def test_three_lists_must_all_agree(self) -> None:
        assert longest_common_prefix([words("a b c"), words("a b d"), words("a b c")]) == 2


class TestCommitPolicy:
    def test_the_first_pass_commits_nothing(self) -> None:
        """One pass is not agreement; there is nothing to agree with yet."""
        agreement = LocalAgreement(2)
        assert agreement.insert(words("of the matrix")) == []
        assert agreement.hypothesis_text == "of the matrix"

    def test_two_agreeing_passes_commit_their_common_prefix(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("of the matrix are always"))
        committed = agreement.insert(words("of the matrix are all real"))

        assert texts(committed) == ["of", "the", "matrix", "are"]
        assert agreement.hypothesis_text == "all real"

    def test_the_disagreeing_tail_is_kept_not_discarded(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("the operators commute"))
        agreement.insert(words("the operators commute only"))
        assert "only" in agreement.hypothesis_text

    def test_a_revised_word_is_never_committed(self) -> None:
        """The whole point: "matrix" becoming "the matrix" must not commit the wrong version."""
        agreement = LocalAgreement(2)
        agreement.insert(words("eigenvalue matrix"))
        committed = agreement.insert(words("eigenvalue the matrix"))
        assert texts(committed) == ["eigenvalue"]

    def test_commits_accumulate_across_many_passes(self) -> None:
        """Each pass commits one more word, leaving the newest as the unagreed tail."""
        agreement = LocalAgreement(2)
        collected: list[str] = []
        for count in range(1, 7):
            collected.extend(texts(agreement.insert(words("a b c d e f")[:count])))

        assert collected == ["a", "b", "c", "d", "e"]
        assert agreement.hypothesis_text == "f"

    def test_committed_words_never_change_afterwards(self) -> None:
        """Constraint C4: committed text is immutable."""
        agreement = LocalAgreement(2)
        agreement.insert(words("the central claim"))
        first = agreement.insert(words("the central claim here"))
        second = agreement.insert(words("the central claim here is"))

        assert texts(first) == ["the", "central", "claim"]
        assert "the" not in texts(second)

    def test_last_committed_end_advances(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("a b c"))
        agreement.insert(words("a b c"))
        assert agreement.last_committed_end == pytest.approx(1.2)


class TestAgreementCount:
    def test_agreement_three_needs_three_passes(self) -> None:
        """Higher counts buy stability at the cost of latency."""
        agreement = LocalAgreement(3)
        assert agreement.insert(words("a b c")) == []
        assert agreement.insert(words("a b c")) == []
        assert texts(agreement.insert(words("a b c"))) == ["a", "b", "c"]

    def test_agreement_three_rejects_a_word_only_two_passes_saw(self) -> None:
        agreement = LocalAgreement(3)
        agreement.insert(words("a b x"))
        agreement.insert(words("a b y"))
        assert texts(agreement.insert(words("a b y"))) == ["a", "b"]

    def test_agreement_one_commits_every_pass(self) -> None:
        agreement = LocalAgreement(1)
        assert texts(agreement.insert(words("a b c"))) == ["a", "b", "c"]
        assert agreement.hypothesis_text == ""

    def test_a_zero_agreement_count_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            LocalAgreement(0)


class TestOverlapSuppression:
    def test_re_transcribed_committed_audio_is_dropped_by_time(self) -> None:
        """Trimming retains an acoustic tail, so the model re-emits those words next pass."""
        agreement = LocalAgreement(2)
        agreement.insert(words("alpha beta", start=0.0))
        agreement.insert(words("alpha beta", start=0.0))
        assert agreement.last_committed_end == pytest.approx(0.8)

        again = agreement.insert(words("alpha beta gamma", start=0.0))
        assert "alpha" not in texts(again)

    def test_re_transcribed_text_is_dropped_even_with_shifted_timings(self) -> None:
        """A re-transcription may carry slightly different times, so text matching is needed too."""
        agreement = LocalAgreement(2)
        agreement.insert(words("the deficiency indices", start=0.0))
        agreement.insert(words("the deficiency indices", start=0.0))

        # Same words, times nudged forward past the tolerance — only the text betrays the repeat.
        repeat = words("the deficiency indices differ", start=1.3)
        assert texts(agreement.insert(repeat)) == []
        assert agreement.hypothesis_text == "differ"

    def test_genuinely_repeated_speech_is_not_suppressed_indefinitely(self) -> None:
        """A speaker really can say the same phrase twice, minutes apart."""
        agreement = LocalAgreement(2)
        agreement.insert(words("in other words", start=0.0))
        agreement.insert(words("in other words", start=0.0))

        later = words("and so we return", start=100.0)
        agreement.insert(later)
        assert texts(agreement.insert(later)) == ["and", "so", "we", "return"]


class TestForceCommit:
    def test_force_commit_returns_the_pending_hypothesis(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("continuous speech with no pause"))
        assert texts(agreement.force_commit()) == ["continuous", "speech", "with", "no", "pause"]
        assert agreement.hypothesis_text == ""

    def test_force_commit_with_nothing_pending_is_empty(self) -> None:
        assert LocalAgreement(2).force_commit() == []

    def test_force_committed_words_are_not_re_emitted(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("alpha beta gamma"))
        agreement.force_commit()

        again = agreement.insert(words("alpha beta gamma delta"))
        assert "alpha" not in texts(again)

    def test_force_commit_clears_the_agreement_window(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("a b"))
        agreement.force_commit()
        assert agreement.insert(words("c d")) == []


class TestTruncationAndReset:
    def test_truncating_the_hypothesis_keeps_a_prefix(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("and so and so and so"))
        agreement.truncate_hypothesis(2)
        assert agreement.hypothesis_text == "and so"

    def test_reset_forgets_everything(self) -> None:
        agreement = LocalAgreement(2)
        agreement.insert(words("a b c"))
        agreement.insert(words("a b c"))
        agreement.reset()

        assert agreement.hypothesis_text == ""
        assert agreement.last_committed_end == 0.0
        assert agreement.insert(words("a b c")) == []
