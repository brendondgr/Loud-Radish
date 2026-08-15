"""Catching text the model invented rather than heard.

Whisper does not fail on audio with no speech in it. It returns, confidently and with plausible
word timings, the phrases its training data ends with — "Thank you", "Bye", "Thanks for watching",
"Subtitles by …" — and a bare "you". Nothing downstream can tell those from speech by reading the
words, which is the whole problem: they arrive looking exactly like a transcript.

Three tests, in order of how much they can be trusted:

1. **The model's own verdict.** ``no_speech_prob`` is Whisper's estimate that a window contained no
   speech, and it is usually decisive — it is near 1.0 on exactly the passes that invent text. It is
   read together with ``avg_logprob``, which is strongly negative when the model was guessing.
2. **Word confidence.** Off by default. A blunt instrument, and it punishes quiet or accented
   speech, which is precisely the speech least well served by being deleted.
3. **The phrase list.** Crude, and it exists because the first two do not catch everything: a short
   burst of noise can score as speech and still decode to "Thank you." It applies **only** when a
   listed phrase is the entire output of a short pass, so a real thank-you inside a sentence is
   untouched.

**Every one of these can in principle delete something real.** That governs the whole module: the
defaults are conservative, each test is switchable on its own, nothing is discarded without a
recorded reason, and a signal the model did not report is read as *no opinion* rather than as
grounds to drop. A filter that silently removes speech is a worse bug than the one it fixes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...config.schema import HallucinationConfig
from .contract import AsrResult

#: Phrases Whisper emits over non-speech, normalised to lower case with punctuation removed.
#:
#: They come from its training data — captioned video — which is why they are all sign-offs and
#: channel boilerplate. This list is deliberately short and literal: every entry is something
#: observed on silence, not something that merely looks like filler. Adding a common English phrase
#: here would delete it from real talks.
HALLUCINATION_PHRASES: frozenset[str] = frozenset(
    {
        "you",
        "thank you",
        "thank you very much",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "thanks for watching and i will see you in the next video",
        "bye",
        "bye bye",
        "goodbye",
        "okay",
        "so",
        "yeah",
        "hmm",
        "mm",
        "uh",
        "um",
        "please subscribe",
        "subscribe to my channel",
        "subtitles by the amaraorg community",
        "amaraorg",
        "transcription by castingwordscom",
        "the end",
        "music",
        "applause",
        "silence",
        "background noise",
    }
)

#: Everything that is not a letter, a digit, or a space. Whisper's punctuation and casing vary
#: between passes over the same audio, so matching has to be blind to both.
_NOT_WORD = re.compile(r"[^\w\s]+", re.UNICODE)


@dataclass(frozen=True)
class Suppression:
    """Why one pass's output was thrown away. Recorded rather than silently applied."""

    #: Short machine-readable cause: ``no-speech``, ``low-confidence``, or ``known-phrase``.
    code: str
    #: One sentence naming what was dropped and on what grounds, for the log.
    reason: str
    #: The text that was discarded, so a wrongly-suppressed passage is recoverable from the log.
    text: str


def normalise_phrase(text: str) -> str:
    """Lower-case, strip punctuation, and collapse whitespace, for blocklist comparison."""
    return " ".join(_NOT_WORD.sub(" ", (text or "").lower()).split())


def judge(
    result: AsrResult, audio_seconds: float, config: HallucinationConfig
) -> Suppression | None:
    """Decide whether this pass is invented. ``None`` means keep it.

    Args:
        result: what the backend returned, including its own confidence in it.
        audio_seconds: how much audio the pass covered. The phrase test needs it — the same words
            over twenty seconds of audio are a sentence, and over one second are an artefact.
        config: the thresholds, read fresh so a change in settings applies to the next pass.
    """
    if not config.enabled or result.is_empty():
        return None

    if _model_says_no_speech(result, config):
        return Suppression(
            code="no-speech",
            reason=(
                f"The speech model scored this audio as {result.no_speech_prob:.0%} likely to "
                f"contain no speech, above the {config.no_speech_threshold:.0%} threshold."
            ),
            text=result.text,
        )

    low = _mean_word_confidence(result)
    if config.min_word_confidence > 0 and low is not None and low < config.min_word_confidence:
        return Suppression(
            code="low-confidence",
            reason=(
                f"The recognised words averaged {low:.0%} confidence, below the "
                f"{config.min_word_confidence:.0%} threshold."
            ),
            text=result.text,
        )

    if config.drop_phrases_enabled and _is_known_phrase(result, audio_seconds, config):
        return Suppression(
            code="known-phrase",
            reason=(
                f'"{result.text}" was the entire output of a '
                f"{audio_seconds:.1f} s pass, and is a phrase this model emits over silence."
            ),
            text=result.text,
        )

    return None


def _model_says_no_speech(result: AsrResult, config: HallucinationConfig) -> bool:
    """Whether the model's own numbers condemn this pass.

    Two tiers, and the second exists because the first was measured and found wanting. In the
    ordinary tier ``no_speech_prob`` is the primary signal and ``avg_logprob`` corroborates it. But
    a run of `tiny` over a non-speech fixture invented "Oh" at a no-speech score of 0.901 with a
    log-probability of −0.99 — condemned by the primary signal, acquitted by corroboration, missing
    the threshold by a hundredth. Past :attr:`~HallucinationConfig.no_speech_certain` the model is
    no longer hedging and its verdict stands unaided.

    When the model reported no log-probability at all, the primary signal also stands alone:
    requiring corroboration that is structurally unavailable would switch the check off in silence.
    """
    if result.no_speech_prob is None:
        return False
    if result.no_speech_prob >= config.no_speech_certain:
        return True
    if result.no_speech_prob < config.no_speech_threshold:
        return False
    return result.avg_logprob is None or result.avg_logprob <= config.logprob_threshold


def _mean_word_confidence(result: AsrResult) -> float | None:
    """Average word confidence, or ``None`` when the backend does not report any."""
    scores = [word.confidence for word in result.words if word.confidence is not None]
    return sum(scores) / len(scores) if scores else None


def _is_known_phrase(result: AsrResult, audio_seconds: float, config: HallucinationConfig) -> bool:
    """Whether the whole pass is one of the phrases Whisper produces over silence.

    Two conditions, and both matter. The phrase must be the *entire* output — "thank you all for
    coming" is not a match, and neither is a thank-you at the end of a real sentence. And the pass
    must be short: the same two words spread over twenty seconds of audio came from a speaker.
    """
    if audio_seconds > config.max_phrase_seconds:
        return False
    return normalise_phrase(result.text) in HALLUCINATION_PHRASES
