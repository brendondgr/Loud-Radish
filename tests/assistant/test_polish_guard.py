"""The guard between the model's answer and the page.

Decoration must go — the pane renders through ``textContent``, so an unstripped ``**`` reaches the
reader as two asterisks. Lists must stay. And a rewrite that came back a third of the length was a
summary, which is the one thing this feature must never silently produce.
"""

from __future__ import annotations

from app.services.polish.guard import (
    MAX_EXPANSION_RATIO,
    collapse_to_paragraph,
    preserves_content,
    reconcile_timestamps,
    strip_decoration,
)


class TestStrippingDecoration:
    def test_bold_and_italics_lose_their_markers_but_keep_their_words(self) -> None:
        assert strip_decoration("The **key** result is *surprising*.") == (
            "The key result is surprising."
        )

    def test_underscore_emphasis_is_stripped_too(self) -> None:
        assert strip_decoration("He called it __the__ _canonical_ case.") == (
            "He called it the canonical case."
        )

    def test_nested_emphasis_is_fully_unwrapped(self) -> None:
        assert strip_decoration("**_both at once_**") == "both at once"

    def test_headings_and_quote_markers_go(self) -> None:
        assert strip_decoration("## Section\n\nHe said this.") == "Section\n\nHe said this."
        assert strip_decoration("> quoted line") == "quoted line"

    def test_code_markers_go_but_their_contents_stay(self) -> None:
        assert strip_decoration("Set `alpha` to zero.") == "Set alpha to zero."
        assert strip_decoration("```\nplain text\n```") == "plain text"

    def test_lists_survive_because_a_speaker_may_have_enumerated(self) -> None:
        polished = strip_decoration("Three reasons:\n\n- cost\n- time\n- accuracy")
        assert polished == "Three reasons:\n\n- cost\n- time\n- accuracy"

    def test_star_bullets_are_normalised_rather_than_read_as_emphasis(self) -> None:
        """The failure this guards against: `* cost` treated as an unterminated italic run."""
        assert strip_decoration("* cost\n* time") == "- cost\n- time"

    def test_paragraph_breaks_survive(self) -> None:
        assert strip_decoration("First point.\n\nSecond point.") == "First point.\n\nSecond point."

    def test_arithmetic_is_not_mistaken_for_emphasis(self) -> None:
        text = "the 3 * 4 grid and the a_1 term"
        assert strip_decoration(text) == text


class TestContentPreservation:
    SOURCE = " ".join(f"word{i}" for i in range(100))

    def test_a_tidied_rewrite_of_similar_length_passes(self) -> None:
        result = " ".join(f"word{i}" for i in range(95))
        check = preserves_content(self.SOURCE, result, min_ratio=0.6)
        assert check.ok
        assert 0.9 < check.ratio < 1.0

    def test_a_summary_is_rejected(self) -> None:
        check = preserves_content(self.SOURCE, "He talked about words.", min_ratio=0.6)
        assert not check.ok
        assert "summarised" in check.reason

    def test_an_empty_answer_is_rejected(self) -> None:
        assert not preserves_content(self.SOURCE, "   ", min_ratio=0.6).ok

    def test_invented_material_is_rejected(self) -> None:
        result = " ".join(f"word{i}" for i in range(int(100 * MAX_EXPANSION_RATIO) + 10))
        check = preserves_content(self.SOURCE, result, min_ratio=0.6)
        assert not check.ok
        assert "added material" in check.reason

    def test_the_floor_is_configurable(self) -> None:
        """The default is a starting point; it is expected to be tuned against a real model."""
        result = " ".join(f"word{i}" for i in range(70))
        assert preserves_content(self.SOURCE, result, min_ratio=0.6).ok
        assert not preserves_content(self.SOURCE, result, min_ratio=0.8).ok

    def test_the_ceiling_is_configurable_too(self) -> None:
        """An instruction list that expands spoken identifiers returns more words than the shipped
        one, and a ceiling nailed to a module constant would discard everything it produced."""
        result = " ".join(f"word{i}" for i in range(300))
        assert not preserves_content(self.SOURCE, result, min_ratio=0.6).ok
        assert preserves_content(self.SOURCE, result, min_ratio=0.6, max_ratio=4.0).ok

    def test_timestamps_are_not_counted_as_words(self) -> None:
        """They are navigation, not speech; counting them measures punctuation."""
        marked_source = "[00:00] " + " ".join(f"word{i}" for i in range(10))
        marked_result = "[00:00] " + " ".join(f"word{i}" for i in range(10))
        assert preserves_content(marked_source, marked_result, min_ratio=0.6).ratio == 1.0

    def test_a_summary_is_still_caught_when_it_keeps_every_marker(self) -> None:
        """The obvious way to game a naive count: keep the markers, drop the speech."""
        marked_source = "[00:00] " + " ".join(f"word{i}" for i in range(20)) + " [00:15] tail"
        check = preserves_content(marked_source, "[00:00] Words. [00:15] Tail.", min_ratio=0.6)
        assert not check.ok


class TestReconcilingTimestamps:
    SUPPLIED = ["00:00", "00:15", "00:30"]

    def test_markers_the_model_carried_through_are_kept_where_it_put_them(self) -> None:
        text = "[00:00] The claim is this, [00:15] and this follows."
        assert reconcile_timestamps(text, self.SUPPLIED) == text

    def test_a_marker_the_model_invented_is_removed(self) -> None:
        """It points at a moment nobody chose, and looks identical to one that does not."""
        text = "[00:00] The claim is this, [00:07] and this follows."
        expected = "[00:00] The claim is this, and this follows."
        assert reconcile_timestamps(text, self.SUPPLIED) == expected

    def test_a_marker_running_backwards_is_removed(self) -> None:
        """Time in one minute of one talk only moves forwards."""
        text = "[00:15] First. [00:00] Second."
        assert reconcile_timestamps(text, self.SUPPLIED) == "[00:15] First. Second."

    def test_an_immediate_repeat_is_removed(self) -> None:
        text = "[00:15] [00:15] Only once."
        assert reconcile_timestamps(text, self.SUPPLIED) == "[00:15] Only once."

    def test_a_model_that_dropped_every_marker_still_leaves_a_locatable_block(self) -> None:
        text = "The claim is this, and this follows."
        assert reconcile_timestamps(text, self.SUPPLIED, fallback="00:00") == f"[00:00] {text}"

    def test_the_fallback_is_not_added_when_a_marker_survived(self) -> None:
        text = "[00:30] Only the last one."
        assert reconcile_timestamps(text, self.SUPPLIED, fallback="00:00") == text

    def test_nothing_is_invented_for_an_empty_answer(self) -> None:
        assert reconcile_timestamps("", self.SUPPLIED, fallback="00:00") == ""

    def test_removing_a_marker_does_not_leave_a_double_space(self) -> None:
        assert "  " not in reconcile_timestamps("A [09:99] b", self.SUPPLIED)


class TestCollapsingToOneParagraph:
    def test_several_paragraphs_become_one(self) -> None:
        """The requirement: a transcript that breaks to a new line constantly is disruptive."""
        text = "First point.\n\nSecond point.\n\nThird point."
        assert collapse_to_paragraph(text) == "First point. Second point. Third point."

    def test_single_line_breaks_are_joined_too(self) -> None:
        assert collapse_to_paragraph("one\ntwo\nthree") == "one two three"

    def test_a_list_keeps_its_shape(self) -> None:
        """A speaker who enumerated three things enumerated them; flattening changes the meaning."""
        text = "Three reasons:\n\n- cost\n- time\n- accuracy"
        assert collapse_to_paragraph(text) == "Three reasons:\n\n- cost\n- time\n- accuracy"

    def test_prose_on_both_sides_of_a_list_stays_separate_from_it(self) -> None:
        text = "Before.\n\n- one\n- two\n\nAfter. \n\nAlso after."
        assert collapse_to_paragraph(text) == "Before.\n\n- one\n- two\n\nAfter. Also after."

    def test_timestamps_survive_the_collapse(self) -> None:
        text = "[00:00] First.\n\n[00:15] Second."
        assert collapse_to_paragraph(text) == "[00:00] First. [00:15] Second."

    def test_an_empty_answer_stays_empty(self) -> None:
        assert collapse_to_paragraph("") == ""
        assert collapse_to_paragraph("\n\n  \n") == ""
