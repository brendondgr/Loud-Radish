"""LocalAgreement-*n* — the rule for promoting hypothesis text to committed text (BE §7.3).

The obvious approach to streaming an offline model — "transcribe the last five seconds, repeat" —
fails twice over. A window boundary lands mid-word and the model guesses or drops it; fixing that
with overlapping windows means consecutive outputs cover the same audio, and deciding what is new is
not string de-duplication, because **the model revises its own output as more context arrives.**
"matrix" in one pass becomes "the matrix" in the next.

So the question is reframed. Not *"what did the last five seconds say?"* but *"what am I now
confident enough to write down permanently?"* — and the answer is:

    Run inference on the unconfirmed buffer. Compare this pass's output with the previous pass's.
    Find the longest common prefix. Commit it. Keep the remainder as hypothesis.

::

    pass N     : "of  the  matrix  are  always"
    pass N+1   : "of  the  matrix  are  all  real"
                  └──── agree ─────┘  └── diverge ──┘

    commit     : "of the matrix are"      → permanent transcript
    hypothesis : "all real"               → greyed tail, may change

Two passes over overlapping but *different* audio spans independently produced the same words. That
agreement is strong evidence. The disagreeing tail waits for more evidence.

The "-*n*" is how many consecutive passes must agree. Two is the sweet spot; higher values buy
stability at the cost of latency. Expect 2–4 seconds of latency from this and design the UI
accordingly — it is inherent to the approach, not a defect.
"""

from __future__ import annotations

from collections import deque

from ..asr.contract import WordToken

#: Characters stripped before comparing two words. A model that adds a comma in the second pass has
#: not changed its mind about the word, and blocking the commit on that would stall the transcript.
PUNCTUATION = ".,!?;:\"'()[]{}—–-…"

#: Words starting earlier than the last committed word's end, minus this slack, are treated as
#: already-committed audio being re-transcribed after a trim retained acoustic context.
OVERLAP_TOLERANCE = 0.1


def normalise(text: str) -> str:
    """Reduce a word to what matters for comparison: case and punctuation are noise here."""
    stripped = text.strip().strip(PUNCTUATION).lower()
    return stripped or text.strip().lower()


def strip_repeated_prefix(words: list[WordToken], emitted_tail: list[str]) -> list[WordToken]:
    """Drop a leading run of ``words`` that repeats the end of what was already emitted.

    Both engines need this. The offline path retains a tail of committed audio as acoustic context,
    so the model re-transcribes it; the bypass path can be handed a word that spans a chunk
    boundary. Either way the transcript must not gain a duplicate.

    Matching on text rather than timestamps is deliberate: a re-transcription carries times that
    have been clamped or nudged by the window it arrived in, so the timings disagree even when the
    word plainly does not.

    Args:
        words: incoming words, normalised for comparison internally.
        emitted_tail: normalised text of the most recently emitted words, oldest first.
    """
    if not words or not emitted_tail:
        return words

    incoming = [normalise(word.text) for word in words]
    # Longest overlap first: a short spurious match is more likely than a long one.
    for length in range(min(len(emitted_tail), len(incoming)), 0, -1):
        if emitted_tail[-length:] == incoming[:length]:
            return words[length:]
    return words


def longest_common_prefix(word_lists: list[list[WordToken]]) -> int:
    """How many leading words every list in ``word_lists`` agrees on."""
    if not word_lists or any(not words for words in word_lists):
        return 0

    shortest = min(len(words) for words in word_lists)
    for index in range(shortest):
        first = normalise(word_lists[0][index].text)
        if any(normalise(words[index].text) != first for words in word_lists[1:]):
            return index
    return shortest


class LocalAgreement:
    """Accumulates passes and commits what consecutive passes agree on.

    Words handed in must carry **session-absolute** timestamps. The engine rebases before calling
    here, so this class never has to reason about where the buffer currently starts.
    """

    def __init__(self, agreement_count: int = 2) -> None:
        if agreement_count < 1:
            raise ValueError("Agreement count must be at least 1")
        self._n = agreement_count
        # One fewer than the agreement count: the incoming pass is the last vote.
        self._window: deque[list[WordToken]] = deque(maxlen=max(1, agreement_count - 1))
        self._pending: list[WordToken] = []
        self._last_committed_end = 0.0
        self._committed_tail: list[str] = []

    # -- state ---------------------------------------------------------------------

    @property
    def hypothesis(self) -> list[WordToken]:
        """The tentative tail — displayed distinctly, and may be rewritten next pass."""
        return list(self._pending)

    @property
    def hypothesis_text(self) -> str:
        """The tentative tail as one string."""
        return " ".join(word.text for word in self._pending).strip()

    @property
    def last_committed_end(self) -> float:
        """Session-absolute end of the last committed word. Zero before anything commits."""
        return self._last_committed_end

    # -- the rule ------------------------------------------------------------------

    def insert(self, words: list[WordToken]) -> list[WordToken]:
        """Feed one pass's output and return whatever it made safe to commit."""
        fresh = self._drop_already_committed(words)

        if self._n == 1:
            # Agreement-1 commits every pass outright. Included for completeness and for
            # streaming-native backends driven through this class; not recommended for Whisper.
            self._commit(fresh)
            self._pending = []
            return fresh

        votes = [*self._window, fresh]
        agreed = longest_common_prefix(votes) if len(votes) >= self._n else 0

        committed = fresh[:agreed]
        if committed:
            self._commit(committed)

        # Every stored vote agreed on the committed prefix, so drop it from each of them; what
        # remains is directly comparable with the next pass.
        remainder = [vote[agreed:] for vote in self._window]
        self._window.clear()
        self._window.extend(remainder)
        self._window.append(fresh[agreed:])

        self._pending = fresh[agreed:]
        return committed

    def force_commit(self) -> list[WordToken]:
        """Commit the pending hypothesis without waiting for agreement.

        The escape hatch behind the maximum-buffer, commit-timeout, silence-gate, and repetition
        guards. Every one of those is a case where waiting for agreement produces a worse outcome
        than committing text that might still have changed.
        """
        pending = self._pending
        if pending:
            self._commit(pending)
        self._pending = []
        self._window.clear()
        return pending

    def truncate_hypothesis(self, keep: int) -> None:
        """Discard all but the first ``keep`` pending words, used by the repetition filter."""
        self._pending = self._pending[:keep]
        self._window.clear()
        if self._pending:
            self._window.append(list(self._pending))

    def reset(self) -> None:
        """Forget everything. Used on session start and on a model swap."""
        self._window.clear()
        self._pending = []
        self._last_committed_end = 0.0
        self._committed_tail = []

    # -- internals -----------------------------------------------------------------

    def _commit(self, words: list[WordToken]) -> None:
        """Record a commit and remember its tail for overlap suppression."""
        if not words:
            return
        self._last_committed_end = max(self._last_committed_end, words[-1].end)
        self._committed_tail = (self._committed_tail + [normalise(w.text) for w in words])[-12:]

    def _drop_already_committed(self, words: list[WordToken]) -> list[WordToken]:
        """Remove re-transcription of audio that has already been committed.

        Trimming retains a short tail of committed audio as acoustic context, which improves the
        first word after the cut — and means the model re-emits those words on the next pass. They
        are dropped two ways, because either alone lets duplicates through: by timestamp, and then
        by matching the text against the tail of what was committed, since a re-transcription may
        carry slightly different timings.
        """
        by_time = [
            word for word in words if word.end > self._last_committed_end + OVERLAP_TOLERANCE
        ]
        return strip_repeated_prefix(by_time, self._committed_tail)
