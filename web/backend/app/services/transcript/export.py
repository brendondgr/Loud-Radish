"""Export formats (BE §17.2).

Five, each for a different afterwards:

* **Plain text** — timestamped, paragraph-broken. For pasting anywhere.
* **Markdown** — transcript plus outline plus glossary. The most useful format for later study, and
  the one that justifies keeping all three in one file.
* **SRT** and **VTT** — subtitle formats, if the user recorded corresponding video.
* **JSON** — everything, for reprocessing.

**The conversation is not one of them, and that is a change.** Markdown and JSON can still carry
the questions the user asked the assistant during the recording, and their callers still pass it
when it is wanted — but the routes no longer pass it by default, because a transcript export is
mostly something being handed to somebody else and the user's own half of a conversation is not
part of the record of the talk. It exports on its own instead: :func:`to_chat_markdown` and
:func:`to_chat_json`, reached through ``GET /api/sessions/{key}/chat``.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from ...models.segment import Segment
from ...models.session import ChatMessage, GlossaryTerm, SessionMetadata, Summary

ExportFormat = Literal["text", "markdown", "srt", "vtt", "json"]

#: MIME type and file extension per format, for the download response.
FORMAT_INFO: dict[str, tuple[str, str]] = {
    "text": ("text/plain; charset=utf-8", "txt"),
    "markdown": ("text/markdown; charset=utf-8", "md"),
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "vtt": ("text/vtt; charset=utf-8", "vtt"),
    "json": ("application/json; charset=utf-8", "json"),
}


def format_timestamp(seconds: float) -> str:
    """Render session-relative seconds as ``HH:MM:SS``."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _subtitle_time(seconds: float, separator: str) -> str:
    """Render a subtitle timestamp with milliseconds."""
    total = max(0.0, seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    whole = int(secs)
    millis = int(round((secs - whole) * 1000))
    if millis == 1000:  # rounding carried into the next second
        whole += 1
        millis = 0
    return f"{int(hours):02d}:{int(minutes):02d}:{whole:02d}{separator}{millis:03d}"


def to_text(segments: list[Segment], metadata: SessionMetadata | None = None) -> str:
    """Timestamped, paragraph-broken plain text."""
    lines: list[str] = []
    if metadata is not None:
        lines.extend(_header_lines(metadata))
        lines.append("")

    for segment in segments:
        lines.append(f"[{format_timestamp(segment.start)}] {segment.text}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def to_markdown(
    segments: list[Segment],
    metadata: SessionMetadata | None = None,
    summaries: list[Summary] | None = None,
    glossary: list[GlossaryTerm] | None = None,
    chat: list[ChatMessage] | None = None,
) -> str:
    """Transcript, outline, glossary, and conversation in one document.

    Ordered outline-first because that is the reading order that makes sense afterwards: what the
    talk argued, what the terms meant, then the verbatim record, then what was asked about it.
    """
    title = (metadata.title if metadata and metadata.title else "Seminar transcript").strip()
    lines: list[str] = [f"# {title}", ""]

    if metadata is not None:
        details = [detail for detail in _header_lines(metadata) if detail]
        if details:
            lines.extend(f"*{detail}*  " for detail in details)
            lines.append("")

    if summaries:
        lines.extend(["## Outline", ""])
        for summary in summaries:
            lines.append(f"**{format_timestamp(summary.start)}** — {summary.text}")
            lines.append("")

    if glossary:
        lines.extend(["## Glossary", ""])
        for term in glossary:
            lines.append(
                f"- **{term.term}** — {term.definition} "
                f"*(first used {format_timestamp(term.first_seen)})*"
            )
        lines.append("")

    lines.extend(["## Transcript", ""])
    for segment in segments:
        lines.append(f"**{format_timestamp(segment.start)}**  {segment.text}")
        lines.append("")

    if chat:
        lines.extend(["## Conversation", ""])
        for message in chat:
            speaker = "You" if message.role == "user" else "Assistant"
            lines.append(f"**{speaker}:** {message.text}")
            if message.context_timestamp is not None:
                lines.append(
                    f"*Based on the transcript to {format_timestamp(message.context_timestamp)}.*"
                )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def to_srt(segments: list[Segment]) -> str:
    """SubRip subtitles, numbered from one."""
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n"
            f"{_subtitle_time(segment.start, ',')} --> {_subtitle_time(segment.end, ',')}\n"
            f"{segment.text}\n"
        )
    return "\n".join(blocks)


def to_vtt(segments: list[Segment]) -> str:
    """WebVTT subtitles."""
    blocks = ["WEBVTT", ""]
    for segment in segments:
        blocks.append(
            f"{_subtitle_time(segment.start, '.')} --> {_subtitle_time(segment.end, '.')}\n"
            f"{segment.text}\n"
        )
    return "\n".join(blocks)


def to_json(
    segments: list[Segment],
    metadata: SessionMetadata | None = None,
    summaries: list[Summary] | None = None,
    glossary: list[GlossaryTerm] | None = None,
    chat: list[ChatMessage] | None = None,
    include_words: bool = True,
) -> str:
    """Everything, for reprocessing — including word-level detail by default."""
    payload: dict[str, Any] = {
        "session": metadata.as_dict() if metadata else None,
        "segments": [segment.as_event(include_words=include_words) for segment in segments],
        "summaries": [summary.as_event() for summary in (summaries or [])],
        "glossary": [term.as_event() for term in (glossary or [])],
        "chat": [message.as_dict() for message in (chat or [])],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


#: The conversation's own two formats. Deliberately not members of :data:`FORMAT_INFO`: they carry
#: no transcript at all, so offering them in the same list as SRT would invite someone to pick one
#: and get a file with none of the talk in it.
CHAT_FORMAT_INFO: dict[str, tuple[str, str]] = {
    "markdown": ("text/markdown; charset=utf-8", "md"),
    "json": ("application/json; charset=utf-8", "json"),
}


def to_chat_markdown(chat: list[ChatMessage], metadata: SessionMetadata | None = None) -> str:
    """The conversation on its own, for reading.

    Each answer keeps the transcript position it was based on, which is what makes it re-checkable
    against the talk — an answer about the last ten minutes means nothing without knowing which ten.
    """
    title = (metadata.title if metadata and metadata.title else "Seminar").strip()
    lines: list[str] = [f"# {title} — conversation", ""]

    if metadata is not None:
        details = [detail for detail in _header_lines(metadata) if detail]
        if details:
            lines.extend(f"*{detail}*  " for detail in details)
            lines.append("")

    if not chat:
        lines.extend(["*Nothing was asked during this recording.*", ""])
        return "\n".join(lines).rstrip() + "\n"

    for message in chat:
        speaker = "You" if message.role == "user" else "Assistant"
        lines.append(f"**{speaker}:** {message.text}")
        if message.context_timestamp is not None:
            lines.append(
                f"*Based on the transcript to {format_timestamp(message.context_timestamp)}.*"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def to_chat_json(chat: list[ChatMessage], metadata: SessionMetadata | None = None) -> str:
    """The conversation on its own, for reprocessing."""
    payload: dict[str, Any] = {
        "session": metadata.as_dict() if metadata else None,
        "chat": [message.as_dict() for message in chat],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def export_chat(
    fmt: str, chat: list[ChatMessage], metadata: SessionMetadata | None = None
) -> tuple[str, str, str]:
    """Render the conversation. Returns ``(body, mime_type, extension)``."""
    if fmt not in CHAT_FORMAT_INFO:
        raise ValueError(
            f"Unknown chat export format {fmt!r}. Available: {', '.join(sorted(CHAT_FORMAT_INFO))}."
        )
    body = to_chat_markdown(chat, metadata) if fmt == "markdown" else to_chat_json(chat, metadata)
    mime, extension = CHAT_FORMAT_INFO[fmt]
    return body, mime, extension


def export(
    fmt: str,
    segments: list[Segment],
    metadata: SessionMetadata | None = None,
    summaries: list[Summary] | None = None,
    glossary: list[GlossaryTerm] | None = None,
    chat: list[ChatMessage] | None = None,
) -> tuple[str, str, str]:
    """Render one format.

    Returns:
        ``(body, mime_type, extension)``.

    Raises:
        ValueError: for an unknown format, listing the ones that exist.
    """
    if fmt not in FORMAT_INFO:
        raise ValueError(
            f"Unknown export format {fmt!r}. Available: {', '.join(sorted(FORMAT_INFO))}."
        )

    if fmt == "text":
        body = to_text(segments, metadata)
    elif fmt == "markdown":
        body = to_markdown(segments, metadata, summaries, glossary, chat)
    elif fmt == "srt":
        body = to_srt(segments)
    elif fmt == "vtt":
        body = to_vtt(segments)
    else:
        body = to_json(segments, metadata, summaries, glossary, chat)

    mime, extension = FORMAT_INFO[fmt]
    return body, mime, extension


def _header_lines(metadata: SessionMetadata) -> list[str]:
    """The metadata worth putting at the top of an export."""
    lines = []
    if metadata.speaker:
        lines.append(f"Speaker: {metadata.speaker}")
    if metadata.venue:
        lines.append(f"Venue: {metadata.venue}")
    lines.append(f"Recorded: {metadata.started_at.strftime('%Y-%m-%d %H:%M')}")
    return lines
