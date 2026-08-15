"""The minute-by-minute polish pass — the transcript rewritten for reading (D-018).

Speech models emit fragments. A committed segment is two or three seconds of speech with whatever
punctuation the model guessed at, and a page of those is a wall of half-sentences. This package
takes roughly a minute of *committed* transcript at a time, cuts it at a natural break in speech,
and asks the language model to repair punctuation, sentence flow, and paragraphing — and nothing
else.

**It is not summarisation.** :mod:`..context` already summarises, and summarising is lossy by
design. This must not drop a single claim, which is why the prompt is an instruction list rather
than a request, and why :mod:`.guard` checks the result before it is stored.

**It is optional at every level.** No language model, an unreachable one, a failed call, or a
result that fails the guard all produce the same outcome: no polished block, and the page keeps
showing the raw segments it already shows today.
"""

from .chunker import CutDecision, decide_cut
from .guard import ContentCheck, preserves_content, strip_decoration

__all__ = [
    "ContentCheck",
    "CutDecision",
    "decide_cut",
    "preserves_content",
    "strip_decoration",
]
