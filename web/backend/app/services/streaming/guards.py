"""The six guards (BE §7.6).

Each prevents a specific real failure. Implement all of them.

===================== ============================================ ==========================
Guard                 Trigger                                      Prevents
===================== ============================================ ==========================
Minimum buffer        buffer shorter than ~1 s                     wasted passes on fragments
Maximum buffer        buffer longer than ~25 s                     exceeding the model window
Commit timeout        no commit for ~15 s despite speech           a UI that appears frozen
Silence gate          the VAD reports no speech                    hallucinated text on silence
Repetition filter     an n-gram repeats more than three times      degenerate looping
Queue backpressure    audio queue depth rising                     silent unbounded lag
===================== ============================================ ==========================

The backpressure guard deserves emphasis. If real-time factor exceeds 1 (constraint **C3**) the
system will never catch up on its own. Detect it and either degrade gracefully or tell the user
plainly — do not fail silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ...config.schema import StreamingConfig
from ..asr.contract import WordToken
from .agreement import normalise


class GuardAction(StrEnum):
    """What a guard decided should happen to this iteration."""

    PROCEED = "proceed"
    #: Skip inference entirely — nothing useful would come of it.
    SKIP = "skip"
    #: Skip inference and commit whatever hypothesis is pending.
    SKIP_AND_COMMIT = "skip-and-commit"
    #: Run nothing; force-commit the hypothesis and hard-trim the buffer.
    FORCE_COMMIT = "force-commit"


@dataclass(frozen=True)
class GuardDecision:
    """One guard verdict, with the reason attached for logging and telemetry."""

    action: GuardAction
    #: Which guard fired, or an empty string when none did.
    guard: str = ""
    reason: str = ""

    @property
    def should_infer(self) -> bool:
        """Whether inference should run this iteration."""
        return self.action is GuardAction.PROCEED

    @property
    def should_commit(self) -> bool:
        """Whether the pending hypothesis should be force-committed."""
        return self.action in (GuardAction.SKIP_AND_COMMIT, GuardAction.FORCE_COMMIT)


PROCEED = GuardDecision(action=GuardAction.PROCEED)


class Severity(StrEnum):
    """Matches the transport ``error`` event's three tiers (BE §12.2)."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class GuardWarning:
    """A condition the user needs to know about, phrased as what to do about it."""

    code: str
    message: str
    severity: Severity = Severity.WARNING


class StreamGuards:
    """Holds the guard state for one session."""

    def __init__(self, config: StreamingConfig) -> None:
        self._config = config
        self._last_commit_audio_time = 0.0
        self._backpressure_active = False

    def update_config(self, config: StreamingConfig) -> None:
        """Adopt new streaming settings; all of them are live."""
        self._config = config

    def note_commit(self, audio_time: float) -> None:
        """Record that a commit happened, resetting the timeout."""
        self._last_commit_audio_time = audio_time

    def reset(self, audio_time: float = 0.0) -> None:
        """Clear guard state for a new session."""
        self._last_commit_audio_time = audio_time
        self._backpressure_active = False

    # -- before inference ----------------------------------------------------------

    def before_inference(
        self,
        buffer_seconds: float,
        audio_time: float,
        speaking: bool,
        has_hypothesis: bool,
        has_open_segment: bool = False,
    ) -> GuardDecision:
        """Decide what this iteration should do, in priority order.

        Order matters. The maximum-buffer guard runs first because exceeding the model's window
        produces silently truncated output rather than an error — the worst kind of failure. The
        silence gate runs before the minimum-buffer check so that a pending hypothesis still commits
        when the speaker stops, rather than waiting for audio that is not coming.
        """
        config = self._config

        if buffer_seconds > config.max_buffer_s:
            return GuardDecision(
                action=GuardAction.FORCE_COMMIT,
                guard="maximum-buffer",
                reason=(
                    f"Buffer reached {buffer_seconds:.1f} s, past the {config.max_buffer_s:.0f} s "
                    "limit; committing and hard-trimming."
                ),
            )

        if not speaking:
            # A pause is a segment boundary. Anything still pending — an uncommitted hypothesis or
            # an unfinished segment — is closed here, because waiting for a full stop that will
            # never arrive strands the last thing the speaker said.
            pending = has_hypothesis or has_open_segment
            return GuardDecision(
                action=GuardAction.SKIP_AND_COMMIT if pending else GuardAction.SKIP,
                guard="silence-gate",
                reason="No speech detected; skipping inference to avoid hallucinated text.",
            )

        since_commit = audio_time - self._last_commit_audio_time
        if has_hypothesis and since_commit >= config.commit_timeout_s:
            return GuardDecision(
                action=GuardAction.FORCE_COMMIT,
                guard="commit-timeout",
                reason=(
                    f"No commit for {since_commit:.0f} s of continuous speech; "
                    "committing so the transcript keeps moving."
                ),
            )

        if buffer_seconds < config.min_buffer_s:
            return GuardDecision(
                action=GuardAction.SKIP,
                guard="minimum-buffer",
                reason=f"Only {buffer_seconds:.2f} s buffered; too short to transcribe usefully.",
            )

        return PROCEED

    # -- after inference -----------------------------------------------------------

    def check_repetition(self, words: list[WordToken]) -> int | None:
        """Detect Whisper's degenerate looping and return where to truncate.

        Returns:
            The index to keep up to, or ``None`` when the output looks healthy.
        """
        return find_repetition(words, limit=self._config.repetition_limit)

    # -- backpressure --------------------------------------------------------------

    def check_backpressure(self, real_time_factor: float, queue_fill: float) -> GuardWarning | None:
        """Detect the system falling behind, and say what to do about it.

        Fires on real-time factor rather than queue depth alone: a full queue can be a transient,
        but a factor below 1 sustained means the system will never catch up on its own.
        """
        falling_behind = real_time_factor > 0.0 and real_time_factor < 1.0
        queue_saturated = queue_fill >= 0.9

        if falling_behind or queue_saturated:
            if not self._backpressure_active:
                self._backpressure_active = True
            return GuardWarning(
                code="falling-behind",
                message=(
                    f"Transcription is running at {real_time_factor:.1f}× realtime and will not "
                    "catch up. Switching to a smaller model recovers within a few seconds."
                    if falling_behind
                    else "The audio queue is nearly full; transcription is close to falling behind."
                ),
                severity=Severity.CRITICAL if falling_behind else Severity.WARNING,
            )

        if self._backpressure_active:
            self._backpressure_active = False
            return GuardWarning(
                code="recovered",
                message="Transcription has caught up.",
                severity=Severity.INFO,
            )
        return None


def find_repetition(words: list[WordToken], limit: int = 3, max_ngram: int = 6) -> int | None:
    """Find the point at which output degenerates into a repeated n-gram.

    Whisper-family models sometimes loop, emitting the same phrase indefinitely. Left alone, the
    loop fills the transcript and the buffer never advances, because agreement between two identical
    degenerate passes looks exactly like confidence.

    Args:
        words: the candidate word list.
        limit: how many consecutive repeats are tolerated before truncating.
        max_ngram: longest phrase length considered.

    Returns:
        The number of words to keep, or ``None`` when nothing is looping.
    """
    if limit < 2 or len(words) < (limit + 1):
        return None

    keys = [normalise(word.text) for word in words]

    for size in range(1, min(max_ngram, len(keys) // (limit + 1)) + 1):
        for start in range(len(keys) - size * (limit + 1) + 1):
            phrase = keys[start : start + size]
            repeats = 1
            cursor = start + size
            while cursor + size <= len(keys) and keys[cursor : cursor + size] == phrase:
                repeats += 1
                cursor += size
            if repeats > limit:
                # Keep the phrase once: it was probably said, just not this many times.
                return start + size

    return None
