"""What the model gives back, checked before it is shown.

Two problems, and neither is hypothetical — both were the reason this module exists.

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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    """
    source_words = len(source.split())
    result_words = len(result.split())

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
