"""What the language model is asked to do to a dictated sentence.

**Narrow on purpose.** The polish pass (`services/polish/`) rewrites a talk into readable prose and
is allowed to join sentences and drop filler across a whole minute. Dictation is not that: the user
is composing, the words are theirs, and a model that "improves" them has changed what they said
into what it would have said. So the instruction is punctuation, capitalisation and obvious
disfluency, and nothing else.

The output is pasted straight into a document, so it must be the text and only the text — no
preamble, no quotes around it, no explanation of what was changed.

**Narrow by default, not by decree.** :func:`resolve` reads ``dictation.instructions`` and returns
the text below only when nothing has been written there (D-068). Somebody dictating into a codebase
and somebody dictating a letter want different things from this pass, and the shipped list can only
be right for one of them. What does not change is the fallback: a tidy that fails, overruns, or
comes back the wrong length is discarded and the raw transcript is pasted instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from ...config.schema import AppConfig

DEFAULT_DICTATION_PROMPT: Final = """\
You are cleaning up a short piece of dictated speech so it can be pasted into a document.

Do exactly this and nothing more:
1. Add sentence punctuation and capitalisation.
2. Remove filler words the speaker did not mean to say: "um", "uh", "er", and a repeated word that
   is plainly a stumble ("the the").
3. Fix an obvious mis-transcription of a common word only where the intended word is unambiguous.
4. Write spoken symbols and code the way they are written: "dot py" becomes ".py", "open paren"
   becomes "(".

Do NOT:
- Reword, shorten, expand, summarise, or improve the phrasing. The words are the speaker's.
- Add a greeting, a sign-off, or anything that was not said.
- Explain what you changed, or wrap the result in quotes or code fences.

Reply with the corrected text alone. If the input is empty or is not speech, reply with the input
unchanged."""

#: Sent to servers that expose a reasoning toggle. A dictation is a punctuation pass, and a model
#: that spends 292 thinking tokens on a twenty-word sentence — measured here — turns a keystroke
#: into a coffee break.
NO_REASONING_EXTRAS: Final[dict[str, Any]] = {
    "chat_template_kwargs": {"enable_thinking": False},
    "reasoning_effort": "low",
}


def resolve(config: AppConfig) -> str:
    """The instructions this dictation tidy should follow. Whitespace counts as blank."""
    written = (config.dictation.instructions or "").strip()
    return written or DEFAULT_DICTATION_PROMPT


__all__ = ["DEFAULT_DICTATION_PROMPT", "NO_REASONING_EXTRAS", "resolve"]
