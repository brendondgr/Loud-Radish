"""The five export formats (BE §17.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from app.models.segment import Segment
from app.models.session import ChatMessage, GlossaryTerm, SessionMetadata, Summary
from app.services.transcript.export import (
    FORMAT_INFO,
    export,
    format_timestamp,
    to_json,
    to_markdown,
    to_srt,
    to_text,
    to_vtt,
)

SEGMENTS = [
    Segment(id=1, text="The central claim is that these operators commute.", start=0.0, end=4.5),
    Segment(id=2, text="Outside that subspace the bracket is not defined.", start=4.5, end=9.25),
    Segment(id=3, text="The deficiency indices differ.", start=3605.0, end=3608.5),
]

METADATA = SessionMetadata(
    session_id="s-1",
    title="Operator domains and the uncertainty relation",
    speaker="A. Speaker",
    venue="Lecture Theatre B",
    started_at=datetime(2026, 8, 14, 14, 30, tzinfo=UTC),
)

SUMMARIES = [Summary(id=1, start=0.0, end=300.0, text="He dismantled a standing assumption.")]
GLOSSARY = [
    GlossaryTerm(
        term="Deficiency indices",
        definition="How far an operator is from being self-adjoint.",
        first_seen=3605.0,
    )
]
CHAT = [
    ChatMessage(id=1, role="user", text="What does that mean?"),
    ChatMessage(id=2, role="assistant", text="The counts differ.", context_timestamp=3608.0),
]


class TestTimestamps:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0.0, "00:00:00"), (9.4, "00:00:09"), (65.0, "00:01:05"), (3605.0, "01:00:05")],
    )
    def test_session_time_renders_as_hours_minutes_seconds(
        self, seconds: float, expected: str
    ) -> None:
        assert format_timestamp(seconds) == expected

    def test_a_negative_time_clamps_to_zero(self) -> None:
        assert format_timestamp(-5.0) == "00:00:00"


class TestPlainText:
    def test_every_segment_appears_with_its_timestamp(self) -> None:
        body = to_text(SEGMENTS)
        assert "[00:00:00] The central claim" in body
        assert "[01:00:05] The deficiency indices differ." in body

    def test_metadata_is_included_when_given(self) -> None:
        body = to_text(SEGMENTS, METADATA)
        assert "A. Speaker" in body
        assert "Lecture Theatre B" in body

    def test_an_empty_transcript_produces_a_valid_file(self) -> None:
        assert to_text([]).endswith("\n")


class TestMarkdown:
    def test_it_carries_transcript_outline_glossary_and_chat(self) -> None:
        """The format that justifies keeping all four in one file."""
        body = to_markdown(SEGMENTS, METADATA, SUMMARIES, GLOSSARY, CHAT)

        assert "# Operator domains and the uncertainty relation" in body
        assert "## Outline" in body
        assert "## Glossary" in body
        assert "## Transcript" in body
        assert "## Conversation" in body

    def test_the_outline_comes_before_the_transcript(self) -> None:
        """The reading order that makes sense afterwards: argument, terms, then the record."""
        body = to_markdown(SEGMENTS, METADATA, SUMMARIES, GLOSSARY)
        assert body.index("## Outline") < body.index("## Transcript")

    def test_glossary_entries_carry_their_first_use(self) -> None:
        body = to_markdown(SEGMENTS, METADATA, None, GLOSSARY)
        assert "first used 01:00:05" in body

    def test_a_stale_answer_says_what_it_was_based_on(self) -> None:
        """Transcription continues during generation, so every answer is slightly behind."""
        body = to_markdown(SEGMENTS, METADATA, None, None, CHAT)
        assert "Based on the transcript to 01:00:08" in body

    def test_absent_sections_are_omitted_rather_than_left_empty(self) -> None:
        body = to_markdown(SEGMENTS)
        assert "## Outline" not in body
        assert "## Glossary" not in body
        assert "## Conversation" not in body

    def test_a_missing_title_falls_back_to_something_readable(self) -> None:
        assert to_markdown(SEGMENTS).startswith("# Seminar transcript")


class TestSubtitles:
    def test_srt_blocks_are_numbered_from_one(self) -> None:
        body = to_srt(SEGMENTS)
        assert body.startswith("1\n")
        assert "\n2\n" in body

    def test_srt_uses_comma_for_milliseconds(self) -> None:
        assert "00:00:00,000 --> 00:00:04,500" in to_srt(SEGMENTS)

    def test_vtt_starts_with_its_header(self) -> None:
        assert to_vtt(SEGMENTS).startswith("WEBVTT")

    def test_vtt_uses_a_full_stop_for_milliseconds(self) -> None:
        assert "00:00:04.500 --> 00:00:09.250" in to_vtt(SEGMENTS)

    def test_an_hour_long_offset_renders_correctly(self) -> None:
        assert "01:00:05,000 --> 01:00:08,500" in to_srt(SEGMENTS)

    def test_millisecond_rounding_does_not_produce_a_thousand(self) -> None:
        """0.9999 must roll into the next second, not render as :00,1000."""
        rounded = to_srt([Segment(id=1, text="x", start=0.9999, end=1.0)])
        assert ",1000" not in rounded
        assert "00:00:01,000" in rounded

    def test_empty_input_produces_an_empty_srt(self) -> None:
        assert to_srt([]) == ""


class TestJson:
    def test_it_carries_everything(self) -> None:
        payload = json.loads(to_json(SEGMENTS, METADATA, SUMMARIES, GLOSSARY, CHAT))
        assert len(payload["segments"]) == 3
        assert payload["session"]["title"] == METADATA.title
        assert len(payload["summaries"]) == 1
        assert len(payload["glossary"]) == 1
        assert len(payload["chat"]) == 2

    def test_word_detail_is_included_by_default(self) -> None:
        payload = json.loads(to_json(SEGMENTS))
        assert "words" in payload["segments"][0]

    def test_word_detail_can_be_dropped(self) -> None:
        payload = json.loads(to_json(SEGMENTS, include_words=False))
        assert "words" not in payload["segments"][0]

    def test_it_is_valid_json_with_no_session(self) -> None:
        assert json.loads(to_json(SEGMENTS))["session"] is None

    def test_non_ascii_survives(self) -> None:
        payload = json.loads(to_json([Segment(id=1, text="Schrödinger — naïve", start=0, end=1)]))
        assert payload["segments"][0]["text"] == "Schrödinger — naïve"


class TestDispatch:
    @pytest.mark.parametrize("fmt", sorted(FORMAT_INFO))
    def test_every_format_produces_a_body_and_its_metadata(self, fmt: str) -> None:
        body, mime, extension = export(fmt, SEGMENTS, METADATA, SUMMARIES, GLOSSARY, CHAT)
        assert body.strip()
        assert "/" in mime
        assert extension and "." not in extension

    def test_an_unknown_format_lists_the_ones_that_exist(self) -> None:
        with pytest.raises(ValueError, match="markdown"):
            export("docx", SEGMENTS)

    @pytest.mark.parametrize("fmt", sorted(FORMAT_INFO))
    def test_every_format_handles_an_empty_transcript(self, fmt: str) -> None:
        body, _, _ = export(fmt, [])
        assert isinstance(body, str)
