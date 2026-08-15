"""Flattening a chunk into one timestamped paragraph, before any model sees it.

This is the deterministic half of the polish pass. What it guarantees is the thing the model cannot
be trusted with: that every timestamp in the finished text is a moment the speaker actually began
saying something, rather than one a model worked out.
"""

from __future__ import annotations

from app.models.segment import Segment
from app.services.polish.source import build_source, strip_markers


def segment(index: int, start: float, end: float | None = None, text: str = "") -> Segment:
    return Segment(
        id=index,
        text=text or f"fragment {index}",
        start=start,
        end=end if end is not None else start + 3.0,
    )


class TestFlattening:
    def test_the_chunk_becomes_one_run_of_text(self) -> None:
        source = build_source(
            [
                segment(1, 0.0, text="the central claim is"),
                segment(2, 4.0, text="that they commute"),
            ],
            interval_s=15.0,
        )
        assert "\n" not in source.text
        assert source.text == "[00:00] the central claim is that they commute"

    def test_empty_segments_are_skipped_rather_than_leaving_double_spaces(self) -> None:
        source = build_source(
            [
                segment(1, 0.0, text="something"),
                segment(2, 4.0, text="   "),
                segment(3, 8.0, text="else"),
            ],
            interval_s=15.0,
        )
        assert source.text == "[00:00] something else"

    def test_an_empty_chunk_reports_itself(self) -> None:
        assert build_source([], interval_s=15.0).is_empty
        assert build_source([segment(1, 0.0, text="  ")], interval_s=15.0).is_empty


class TestMarkerPlacement:
    def test_the_chunk_always_opens_with_a_marker(self) -> None:
        """So even a chunk shorter than one interval can be located."""
        source = build_source([segment(1, 612.0, text="a short tail")], interval_s=15.0)
        assert source.labels == ["10:12"]
        assert source.text.startswith("[10:12] ")

    def test_a_marker_lands_roughly_every_interval(self) -> None:
        segments = [segment(i, (i - 1) * 4.0) for i in range(1, 16)]
        source = build_source(segments, interval_s=15.0)
        assert source.labels == ["00:00", "00:16", "00:32", "00:48"]

    def test_markers_are_segment_starts_not_interpolated_boundaries(self) -> None:
        """A marker must be a moment someone started speaking, or clicking it lands in silence."""
        segments = [segment(1, 0.0), segment(2, 40.0), segment(3, 44.0)]
        source = build_source(segments, interval_s=15.0)
        assert source.labels == ["00:00", "00:40"]

    def test_a_longer_interval_produces_fewer_markers(self) -> None:
        segments = [segment(i, (i - 1) * 4.0) for i in range(1, 16)]
        assert build_source(segments, interval_s=60.0).labels == ["00:00"]

    def test_hours_appear_once_there_are_any(self) -> None:
        """Matching the assistant's citation format exactly — one parser reads both."""
        source = build_source([segment(1, 3723.0)], interval_s=15.0)
        assert source.labels == ["1:02:03"]

    def test_a_segment_spanning_a_boundary_does_not_repeat_its_marker(self) -> None:
        segments = [segment(1, 0.0, end=30.0, text="a long unbroken stretch"), segment(2, 30.0)]
        source = build_source(segments, interval_s=15.0)
        assert source.labels == ["00:00", "00:30"]

    def test_every_label_appears_in_the_text_it_reports(self) -> None:
        segments = [segment(i, i * 8.0) for i in range(1, 10)]
        source = build_source(segments, interval_s=15.0)
        for label in source.labels:
            assert f"[{label}]" in source.text

    def test_the_first_label_is_offered_as_a_fallback(self) -> None:
        source = build_source([segment(1, 90.0)], interval_s=15.0)
        assert source.first_label == "01:30"
        assert build_source([], interval_s=15.0).first_label == ""


class TestStrippingMarkers:
    def test_markers_come_out_and_the_words_stay(self) -> None:
        assert strip_markers("[00:00] the claim [00:15] is this") == "the claim is this"

    def test_the_gap_a_marker_leaves_is_closed(self) -> None:
        """Otherwise a word count sees an empty string as a word."""
        assert strip_markers("[00:00][00:15] words") == "words"

    def test_text_with_no_markers_is_unchanged_but_normalised(self) -> None:
        assert strip_markers("plain  text") == "plain text"
        assert strip_markers("") == ""
