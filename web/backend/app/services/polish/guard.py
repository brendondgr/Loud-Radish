"""What the model gives back, checked before it is shown.

Four problems, and none of them is hypothetical — each is why a piece of this module exists.

**Decoration.** The prompt forbids bold and italics; models emit them anyway, roughly one time in
five. The transcript pane renders model output through ``textContent`` and never as markup, so an
unstripped ``**`` would appear on screen as two asterisks. Stripping is done here rather than in the
frontend because it is a property of the text, not of one way of displaying it. **List bullets are
left alone** — a list is a legitimate structure for a speaker who enumerated three things.

**Compression.** A model asked to tidy text will sometimes summarise it instead, and a summary is
precisely what this feature must not produce: it silently deletes claims from what looks like a
transcript. There is no cheap way to verify meaning, but there is a cheap way to catch the common
failure — a rewrite that came back a third of the length was not a rewrite. The check is a floor on
length, not a semantic verifier, and it is stated as such so nobody mistakes it for one. A model
that paraphrases at similar length passes it; the prompt is the only defence against that.

**Invented timestamps.** The model is given markers and asked only to carry them along, but it will
occasionally move one, drop one, duplicate one, or write a new one that looks exactly like the
others. A wrong timestamp is worse than a missing one: it is indistinguishable from a real one until
the reader clicks it and lands somewhere the claim was never made. So the answer's markers are
reconciled against what was actually supplied, and anything unaccounted for is removed.

**Line breaks.** The prompt asks for one continuous paragraph, because a transcript broken into a
new line every few seconds is disruptive to read. Models comply unevenly. Compliance is therefore
enforced here rather than hoped for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .source import MARKER, strip_markers

#: ``**bold**`` and ``__bold__``.
_STRONG = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)

#: ``*italic*`` and ``_italic_``. The lookarounds are what keep a list bullet ("* item", which has a
#: space after the marker) from being read as an unterminated emphasis run.
_EMPHASIS = re.compile(r"(?<![\w*_])([*_])(?=\S)([^*_\n]+?)(?<=\S)\1(?![\w*_])")

#: ``# Heading`` and ``> quote`` at the start of a line.
_LINE_PREFIX = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?)", re.MULTILINE)

#: Fenced code blocks and inline code. Transcript prose is not code; the fence is always spurious.
_FENCE = re.compile(r"^[ \t]*```[^\n]*$", re.MULTILINE)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")

#: A list bullet written with ``*`` or ``+``, normalised to ``-`` so the pane styles one shape.
_BULLET = re.compile(r"^([ \t]*)[*+][ \t]+", re.MULTILINE)

#: Three or more blank lines, left behind once headings and fences are removed.
_BLANK_RUN = re.compile(r"\n{3,}")

#: A rewrite longer than this multiple of the source did not tidy it — it invented. Generous,
#: because expanding contractions and repairing elisions legitimately adds words.
MAX_EXPANSION_RATIO = 1.5


@dataclass(frozen=True)
class ContentCheck:
    """The verdict on one rewrite."""

    ok: bool
    reason: str
    #: Returned words divided by source words. Logged, so a threshold can be tuned from real runs.
    ratio: float


def strip_decoration(text: str) -> str:
    """Remove emphasis, headings, quotes, and code markers; keep lists and paragraphs."""
    cleaned = _FENCE.sub("", text or "")
    cleaned = _INLINE_CODE.sub(r"\1", cleaned)
    cleaned = _STRONG.sub(r"\2", cleaned)
    # Twice: `**_both_**` leaves an emphasis run behind once the strong markers are gone.
    cleaned = _EMPHASIS.sub(r"\2", cleaned)
    cleaned = _EMPHASIS.sub(r"\2", cleaned)
    cleaned = _LINE_PREFIX.sub("", cleaned)
    cleaned = _BULLET.sub(r"\1- ", cleaned)
    cleaned = _BLANK_RUN.sub("\n\n", cleaned)
    return "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()


def preserves_content(source: str, result: str, min_ratio: float) -> ContentCheck:
    """Judge whether ``result`` is a tidied ``source`` rather than a summary of it.

    A length check, not a meaning check. It catches the failure that matters — a model that
    summarised when it was told to tidy — and nothing subtler.

    Timestamp markers are removed from both sides before counting. They are navigation, not speech,
    and a chunk with six of them would otherwise measure as six words of content the model was
    obliged to keep.
    """
    source_words = len(strip_markers(source).split())
    result_words = len(strip_markers(result).split())

    if not result_words:
        return ContentCheck(False, "the model returned nothing", 0.0)
    if not source_words:
        return ContentCheck(False, "there was nothing to polish", 0.0)

    ratio = result_words / source_words
    if ratio < min_ratio:
        return ContentCheck(
            False,
            f"the rewrite kept {ratio:.0%} of the words, below the {min_ratio:.0%} floor — "
            f"the model summarised rather than tidied",
            ratio,
        )
    if ratio > MAX_EXPANSION_RATIO:
        return ContentCheck(
            False,
            f"the rewrite is {ratio:.0%} of the length of what was said — the model added material",
            ratio,
        )
    return ContentCheck(True, "", ratio)


def reconcile_timestamps(text: str, supplied: list[str], fallback: str = "") -> str:
    """Keep only the markers that were given to the model, in an order that can be true.

    Three things are removed, and each one would otherwise produce a timestamp that seeks to the
    wrong place:

    * a marker that was never supplied — the model wrote it, and it points at a moment nobody
      chose;
    * a marker that runs backwards past one already kept — the chunk is a minute of one talk, so
      time in it only moves forwards, and the earlier of the two is the one that has already been
      trusted;
    * a marker immediately repeating the one before it, which is noise rather than navigation.

    If nothing survives, ``fallback`` is prepended so the stretch is still locatable at all. Losing
    a good rewrite over a mishandled marker would be the wrong trade: the raw segments underneath
    carry exact times regardless, and the block's own start is never in doubt.
    """
    allowed = set(supplied)
    order = {label: index for index, label in enumerate(supplied)}
    kept: list[str] = []
    highest = -1

    def keep(match: re.Match[str]) -> str:
        nonlocal highest
        label = match.group(1)
        if label not in allowed:
            return ""
        position = order[label]
        if position < highest or (kept and label == kept[-1]):
            return ""
        highest = position
        kept.append(label)
        return match.group(0)

    cleaned = _collapse_spaces(MARKER.sub(keep, text or ""))
    if kept or not fallback:
        return cleaned
    return f"[{fallback}] {cleaned}".strip() if cleaned else ""


def collapse_to_paragraph(text: str) -> str:
    """Join everything that is not a list into one continuous paragraph.

    The prompt asks for this and models comply unevenly, so it is enforced rather than hoped for: a
    transcript that starts a new line every few seconds is disruptive to read, which is the whole
    reason the polish pass produces prose instead of segments.

    A run of list items keeps its shape. A speaker who enumerated three things enumerated them, and
    flattening that into a sentence would be the pass changing what was said rather than how it
    reads.
    """
    blocks: list[str] = []
    prose: list[str] = []

    for chunk in re.split(r"\n{2,}", text or ""):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        if all(line.startswith("- ") for line in lines):
            if prose:
                blocks.append(" ".join(prose))
                prose = []
            blocks.append("\n".join(lines))
        else:
            prose.append(" ".join(lines))

    if prose:
        blocks.append(" ".join(prose))
    return _collapse_spaces("\n\n".join(blocks))


def _collapse_spaces(text: str) -> str:
    """Tidy the whitespace a removal leaves behind, without touching line structure."""
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.splitlines()]
    return "\n".join(lines).strip()
