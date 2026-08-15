"""Step one: the chunk as a single continuous paragraph, with its timestamps already in it.

The speech model hands over a minute of talk as twenty-odd fragments, each two or three seconds
long. Joining them produces one long run of text that reads as nonsense — no punctuation worth the
name, sentences split across three fragments, filler everywhere. That run is exactly what the model
is asked to rewrite, and building it here rather than in the worker keeps the one part of the pass
that is *deterministic* out of the part that is not.

**Timestamps go in before the model sees them.** The alternative — asking a model to work out when
each sentence was said — produces plausible fabrications, and a fabricated timestamp is worse than
no timestamp because it is indistinguishable from a real one until someone clicks it. So markers are
placed here from the segments' own start times, the model is asked only to carry them along, and
:func:`..guard.reconcile_timestamps` later checks that it did.

The marker format is ``[MM:SS]``, matching what the assistant is told to cite. That is not a
coincidence to be tidied away: the frontend has one parser for clickable timestamps, and text from
these two sources meets it. If the formats diverge, one of them silently stops being clickable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...models.segment import Segment

# The assistant's citation format, defined once. Imported rather than reimplemented so the two can
# never drift apart — see the module docstring.
from ..context.assembler import timestamp

#: A marker as it appears in the text, for finding them again on the way back.
MARKER = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]")


@dataclass(frozen=True)
class PolishSource:
    """One chunk, flattened and marked up, ready to send."""

    #: The whole chunk as a single run of text with ``[MM:SS]`` markers interleaved.
    text: str
    #: Every label placed in :attr:`text`, in order. A marker in the model's answer that is not in
    #: here was invented by the model, and is removed rather than shown.
    labels: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """Whether there is anything to polish."""
        return not self.text.strip()

    @property
    def first_label(self) -> str:
        """The opening marker, used as a fallback when a rewrite comes back with none."""
        return self.labels[0] if self.labels else ""


def build_source(segments: list[Segment], interval_s: float) -> PolishSource:
    """Flatten ``segments`` into one paragraph, marking a timestamp every ``interval_s``.

    The first segment always carries a marker, so every chunk is locatable even when it is shorter
    than one interval. After that a marker is placed on the first segment to start at or after the
    next interval boundary — on the *segment's* start rather than at the exact boundary, so every
    marker is a moment the speaker actually began saying something rather than an interpolation.
    """
    parts: list[str] = []
    labels: list[str] = []
    next_mark = -1.0

    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue

        if segment.start >= next_mark:
            label = timestamp(segment.start)
            # A long segment can span a boundary, and two identical markers in a row is noise the
            # model would then have to carry twice.
            if label != (labels[-1] if labels else None):
                labels.append(label)
                parts.append(f"[{label}]")
            next_mark = segment.start + max(interval_s, 0.0)

        parts.append(text)

    return PolishSource(text=" ".join(parts).strip(), labels=labels)


def strip_markers(text: str) -> str:
    """Remove every ``[MM:SS]`` marker, leaving the words.

    Used wherever the *speech* is what matters rather than its navigation — most importantly the
    length check, which would otherwise be comparing punctuation counts.
    """
    return " ".join(MARKER.sub(" ", text or "").split())
