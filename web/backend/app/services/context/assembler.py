"""Building the prompt for one question, under a token budget (BE §9.4).

A two-hour talk does not fit in any context window, so the question is never "send the transcript"
but "send the right part of it". This module answers that, and the answer is a strict priority
order rather than a scoring function:

1. the question, and any passage the user selected — without these there is nothing to answer;
2. what the speaker has just been saying, **verbatim** — questions are nearly always about this;
3. earlier passages that match the question, retrieved by full-text search;
4. the rolling summaries — the whole talk, compressed;
5. the glossary;
6. recent conversation turns, for follow-up questions.

Two decisions in here are worth defending.

**Nothing is truncated mid-way.** A block that does not fit is dropped whole. Half a transcript
excerpt ending mid-sentence is worse than none of it, because the model treats the fragment as
complete and answers from it.

**What was dropped is reported.** :class:`AssembledContext` carries the list, so the interface can
say "the earliest hour is summarised rather than quoted" instead of silently answering from less
than the user assumes.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from ...config.schema import AppConfig
from ...models.segment import Segment
from ...models.session import ChatMessage, GlossaryTerm, Summary
from ..llm.contract import LlmMessage, system, user
from ..llm.tokens import estimate_tokens
from . import prompts

logger = logging.getLogger(__name__)

#: Fraction of the model's window the context may occupy. The rest is the answer, plus slack for
#: the tokeniser disagreeing with the estimate. Overrunning is not a soft failure: the provider
#: truncates from the front, which removes the system instructions first.
WINDOW_FRACTION = 0.6

#: Conversation turns carried forward. Enough for "and what about the second one?" to work; not so
#: many that an hour-old exchange displaces the transcript.
CHAT_TURNS = 6

#: Retrieved passages per question, before the budget is applied.
MAX_RETRIEVED = 8


class TranscriptSource(Protocol):
    """What the assembler needs from a transcript store.

    Narrower than the store's full surface on purpose: it makes the assembler testable against a
    handful of segments, and keeps a change to the store's persistence from reaching in here.
    """

    def segments_in_range(self, start: float, end: float) -> list[Segment]: ...
    def search(self, query: str, limit: int = 50) -> list[Segment]: ...
    def summaries(self) -> list[Summary]: ...
    def glossary(self) -> list[GlossaryTerm]: ...
    def chat_history(self, limit: int | None = None) -> list[ChatMessage]: ...


@dataclass(frozen=True)
class ContextRequest:
    """One question, and the window of the talk it is about."""

    question: str
    #: Session-relative seconds. The assembler never reads a clock; the caller supplies "now" so the
    #: same request assembles identically in a test and in a live session.
    now: float
    #: A passage the user selected in the transcript, for "ask about this".
    quote: str = ""
    quote_start: float | None = None
    #: Restrict the verbatim window to this many minutes, for a ranged quick action.
    range_minutes: float | None = None
    #: Start of the unread stretch, for "what did I miss".
    since: float | None = None


@dataclass
class AssembledContext:
    """The messages to send, and an account of what did not fit."""

    messages: list[LlmMessage]
    #: The transcript timestamp this answer is current as of. Shown with the answer, because a
    #: reply arriving forty seconds later is about the talk as it stood when it was asked.
    context_timestamp: float
    #: Segment ids the answer may cite, for the frontend to turn into transcript links.
    cites: list[int] = field(default_factory=list)
    #: The session-relative start of every segment actually rendered into the prompt. These are the
    #: only moments an answer may cite; anything else it names was invented rather than read.
    offered_seconds: list[float] = field(default_factory=list)
    #: Human-readable descriptions of what was left out, in the order it was dropped.
    dropped: list[str] = field(default_factory=list)
    estimated_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary, carried on the ``chat.done`` event."""
        return {
            "context_timestamp": self.context_timestamp,
            "cites": list(self.cites),
            "dropped": list(self.dropped),
            "estimated_tokens": self.estimated_tokens,
        }


def effective_budget(config: AppConfig) -> int:
    """The tokens available for context, bounded by the model's window.

    ``context.token_budget`` is what the user asked for; this is what the model can actually take.
    Trusting the configured value alone is how a budget raised for a large model silently overruns
    a small one after the model is switched.
    """
    llm = config.llm
    window = llm.api.context_window if llm.mode == "api" else llm.local.context_window
    room = int(window * WINDOW_FRACTION) - llm.generation.max_output_tokens
    return max(512, min(config.context.token_budget, max(512, room)))


def assemble(
    store: TranscriptSource, config: AppConfig, request: ContextRequest
) -> AssembledContext:
    """Build the messages for one question."""
    budget = effective_budget(config)
    cites: list[int] = []
    offered: list[float] = []
    dropped: list[str] = []
    blocks: list[str] = []
    spent = estimate_tokens(prompts.SYSTEM_PROMPT) + estimate_tokens(request.question)

    def take(header_key: str, body: str, description: str) -> None:
        """Add a labelled block if it fits, and record it if it does not."""
        nonlocal spent
        if not body:
            return
        block = f"{prompts.SECTION_HEADERS[header_key]}\n\n{body}"
        cost = estimate_tokens(block)
        if spent + cost > budget:
            dropped.append(description)
            return
        blocks.append(block)
        spent += cost

    # 1. The selection. Highest priority after the question itself: a question about a passage is
    #    unanswerable without the passage.
    if request.quote:
        take("quote", _quoted(request), "the selected passage")

    # 2. Recent speech, verbatim.
    window_start, window_end = _verbatim_window(config, request)
    recent = store.segments_in_range(window_start, window_end)
    take("recent", _render_segments(recent), _dropped_window(window_start, window_end))
    cites.extend(segment.id for segment in recent)
    offered.extend(segment.start for segment in recent)

    # 3. Earlier passages matching the question. Skipped when the window already covers the whole
    #    talk — retrieving from inside what has already been sent verbatim spends budget on
    #    duplicates.
    if config.context.retrieval_enabled and window_start > 0:
        retrieved = _retrieve(store, request.question, before=window_start)
        take("retrieved", _render_segments(retrieved), "earlier passages matching the question")
        cites.extend(segment.id for segment in retrieved)
        offered.extend(segment.start for segment in retrieved)

    # 4. The outline. Deliberately after retrieval: a summary of an hour is cheap, but a passage
    #    that actually answers the question beats a compression of it.
    summaries = [s for s in store.summaries() if s.start < window_start]
    take("summaries", _render_summaries(summaries), "the summarised outline of earlier material")

    # 5. Terms.
    if config.context.glossary_enabled:
        take("glossary", _render_glossary(store.glossary()), "the glossary")

    messages: list[LlmMessage] = [system(prompts.SYSTEM_PROMPT)]
    if blocks:
        messages.append(system("\n\n".join(blocks)))

    # 6. Conversation history, so a follow-up question makes sense. Added last and charged against
    #    whatever is left, because context about the talk beats context about the conversation.
    for message in _recent_turns(store, budget - spent):
        messages.append(LlmMessage(role=message.role, content=message.text))
        spent += estimate_tokens(message.text)

    messages.append(user(request.question))

    return AssembledContext(
        messages=messages,
        context_timestamp=request.now,
        cites=sorted(set(cites)),
        offered_seconds=sorted(set(offered)),
        dropped=dropped,
        estimated_tokens=spent,
    )


# -- window selection -------------------------------------------------------------------


def _verbatim_window(config: AppConfig, request: ContextRequest) -> tuple[float, float]:
    """The stretch of transcript sent word for word."""
    if request.since is not None:
        return max(0.0, request.since), request.now
    if request.range_minutes:
        return max(0.0, request.now - request.range_minutes * 60.0), request.now
    return max(0.0, request.now - config.context.recent_verbatim_s), request.now


def _retrieve(store: TranscriptSource, question: str, before: float) -> list[Segment]:
    """Full-text search restricted to material older than the verbatim window."""
    if not question.strip():
        return []
    try:
        hits = store.search(question, limit=MAX_RETRIEVED * 3)
    except Exception:  # noqa: BLE001 - a search failure must not cost the user their answer
        return []
    older = [segment for segment in hits if segment.start < before]
    return sorted(older[:MAX_RETRIEVED], key=lambda segment: segment.start)


def _recent_turns(store: TranscriptSource, remaining: int) -> list[ChatMessage]:
    """Conversation turns that fit in what is left, newest kept."""
    if remaining <= 0:
        return []
    turns = store.chat_history(limit=CHAT_TURNS)
    kept: list[ChatMessage] = []
    spent = 0
    for message in reversed(turns):
        cost = estimate_tokens(message.text)
        if spent + cost > remaining:
            break
        kept.insert(0, message)
        spent += cost
    return kept


# -- rendering ---------------------------------------------------------------------------


def timestamp(seconds: float) -> str:
    """Session-relative seconds as ``MM:SS``, matching what the transcript shows.

    Hours are included only once there are any, so a twenty-minute talk is not cited as ``00:12:30``
    when the interface beside it says ``12:30``.
    """
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _render_segments(segments: list[Segment]) -> str:
    """Timestamped transcript lines. The timestamp is what makes a citation checkable."""
    return "\n".join(f"[{timestamp(segment.start)}] {segment.text}" for segment in segments)


def _render_summaries(summaries: list[Summary]) -> str:
    return "\n".join(
        f"[{timestamp(summary.start)}–{timestamp(summary.end)}] {summary.text}"
        for summary in summaries
    )


def _render_glossary(terms: list[GlossaryTerm]) -> str:
    return "\n".join(f"{term.term} — {term.definition}" for term in terms)


def _quoted(request: ContextRequest) -> str:
    at = f" (at {timestamp(request.quote_start)})" if request.quote_start is not None else ""
    return f"{request.quote.strip()}{at}"


def _dropped_window(start: float, end: float) -> str:
    return f"the transcript from {timestamp(start)} to {timestamp(end)}"


#: A cited moment in an answer: ``[12:34]`` or ``[1:02:03]``.
CITATION = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")

#: How far a cited timestamp may be from one that was actually offered and still count as that one.
#: Zero would be defensible — the instruction is to copy — but a model that rounds 12:34.6 to 12:35
#: has understood the transcript correctly and named the right line, and dropping that citation
#: would punish the reader for a rounding.
CITATION_TOLERANCE_S: Final = 2.0


def offered_seconds(segments: Iterable[Segment]) -> set[float]:
    """The timestamps a model was actually shown, which are the only ones it may cite."""
    return {float(segment.start) for segment in segments}


def resolve_citations(answer: str, offered: set[float]) -> tuple[str, list[str]]:
    """Remove cited moments that were never in the transcript the model was given.

    **A wrong timestamp shown confidently is worse than no timestamp.** The reader clicks it, lands
    somewhere unrelated, and now distrusts every citation including the correct ones — which is the
    reported complaint. Stored times were measured and found correct
    (``tests/transcription/test_timestamp_alignment.py``), so a citation that matches nothing was
    invented: the model was given ``[MM:SS]`` markers and produced a number of the same shape
    rather than the same value, most often by interpolating between two lines or by reading one off
    the compressed summary block, whose ranges are not moments at all.

    Returns the answer with unresolvable citations removed, and the list of what was removed. The
    surrounding sentence is deliberately kept — it is usually correct, and it was the *number* that
    was invented, not the claim.
    """
    if not offered:
        return answer, []

    dropped: list[str] = []

    def check(match: re.Match[str]) -> str:
        hours, minutes, seconds = match.groups()
        # `[1:02:03]` is hours:minutes:seconds; `[12:34]` is minutes:seconds.
        total = (
            int(hours) * 3600 + int(minutes) * 60 + int(seconds)
            if seconds is not None
            else int(hours) * 60 + int(minutes)
        )
        if any(abs(total - candidate) <= CITATION_TOLERANCE_S for candidate in offered):
            return match.group(0)
        dropped.append(match.group(0))
        return ""

    cleaned = CITATION.sub(check, answer)
    if dropped:
        logger.info(
            "Dropped %d citation(s) that match no transcript line: %s", len(dropped), dropped
        )
    # Removing a bracketed citation can leave a doubled space or a space before punctuation.
    return re.sub(r" +([,.;:])", r"\1", re.sub(r"  +", " ", cleaned)).strip(), dropped
