"""The guard between the model's answer and the page.

Decoration must go — the pane renders through ``textContent``, so an unstripped ``**`` reaches the
reader as two asterisks. Lists must stay. And a rewrite that came back a third of the length was a
summary, which is the one thing this feature must never silently produce.
"""

from __future__ import annotations

from app.services.polish.guard import MAX_EXPANSION_RATIO, preserves_content, strip_decoration


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
