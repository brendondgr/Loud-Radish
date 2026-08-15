"""When to cut a chunk.

Pure decision logic, deliberately separated from the worker so that "wait about a minute, then wait
for the speaker to stop, then cut" can be tested without a clock, a store, or a language model.

Three rules, in priority order:

1. **Nothing to do.** No committed transcript has arrived since the last cut.
2. **The ceiling.** A speaker in full flow can go several minutes without a qualifying pause, and
   waiting for one indefinitely would leave the page raw for the whole talk. Past the ceiling the
   cut happens wherever the transcript currently ends, mid-flow or not.
3. **The natural break.** Once about a minute has accumulated, wait for the speaker to stop for the
   configured pause and cut there. Cutting mid-sentence is what produces a chunk the model then has
   to guess the end of, and guessing is exactly what must not happen.

**The cut always lands on the end of the newest committed segment**, never on a wall-clock instant.
Audio the engine has not committed yet is still in the hypothesis tail, and cutting past it would
drop those words from the polished text entirely while leaving them in the raw record — the one
outcome worse than not polishing at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...config.schema import PolishConfig


@dataclass(frozen=True)
class CutDecision:
    """Whether to cut now, and why. The reason exists for the worker's debug log."""

    #: Session-relative seconds to cut at, or ``None`` to keep accumulating.
    cut_at: float | None
    reason: str

    @property
    def should_cut(self) -> bool:
        """Whether a chunk is ready to be sent."""
        return self.cut_at is not None


#: Below this there is nothing worth a language-model call: a few seconds of speech is one clause,
#: and asking a model to repair the paragraphing of one clause produces a paraphrase of it.
MIN_CHUNK_SECONDS = 5.0


def decide_cut(
    *,
    cursor: float,
    last_committed_end: float,
    silence_seconds: float,
    config: PolishConfig,
    final: bool = False,
) -> CutDecision:
    """Decide whether the transcript between ``cursor`` and ``last_committed_end`` is ready.

    Args:
        cursor: end of the last chunk that was cut, in session-relative seconds.
        last_committed_end: end of the newest committed segment. The only legal cut point.
        silence_seconds: how long the speaker has currently been silent, from the VAD gate. Zero
            while speaking, and permanently zero when the VAD is disabled — in which case rule 2 is
            the only thing that ever fires, which is correct rather than broken.
        config: the polish settings, read fresh so a change in settings applies on the next tick.
        final: set on the session's last pass, which cuts whatever is left. A talk that ends forty
            seconds into a minute would otherwise lose its closing section — usually the
            conclusions — from the polished page entirely.
    """
    pending = last_committed_end - cursor

    if pending <= 0:
        return CutDecision(None, "nothing new has committed")

    if final:
        if pending < MIN_CHUNK_SECONDS:
            return CutDecision(None, f"final tail is under {MIN_CHUNK_SECONDS:.0f}s")
        return CutDecision(last_committed_end, "final pass")

    if pending >= config.max_chunk_seconds:
        return CutDecision(last_committed_end, f"ceiling of {config.max_chunk_seconds:.0f}s hit")

    if pending < config.chunk_seconds:
        return CutDecision(None, f"{pending:.1f}s of {config.chunk_seconds:.0f}s accumulated")

    if silence_seconds < config.pause_seconds:
        return CutDecision(None, "waiting for a pause in speech")

    return CutDecision(last_committed_end, f"pause of {silence_seconds:.1f}s")
