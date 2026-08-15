"""The polish instruction list.

Written as a numbered list of operations rather than as a request, because the two produce
different behaviour. "Please tidy this transcript" is an invitation to improve it, and a model
offered that invitation rewrites for concision — which here means deleting things the speaker
said. A numbered list of permitted edits, with an explicit statement of what must not change, is
the difference between a cleaned transcript and a summary wearing one's clothes.

The instruction that carries the most weight is **7**. Everything above it is presentation;
7 is the guarantee that the reader is still reading what was actually said. :mod:`.guard` exists
because instruction 7 is not always obeyed.
"""

from __future__ import annotations

from typing import Any, Final

POLISH_PROMPT: Final = """\
You are cleaning up a live speech-to-text transcript so that it can be read. Apply the following \
instructions directly. Do not reason about the task first, do not plan, and do not explain \
yourself — write the cleaned text and nothing else.

1. Remove filler sounds and hesitation noises: "um", "uh", "er", "ah", "mm", and similar.
2. Remove false starts and stutters. Where the speaker abandoned a phrase and restarted, keep only \
the version they completed.
3. Remove accidental repetition — a word or phrase duplicated where the speaker plainly said it \
once. This is a speech model artefact, not speech.
4. Add correct punctuation and capitalisation, and break the run-on text into complete sentences \
at the points the speaker's phrasing indicates.
5. Group the sentences into paragraphs, starting a new one when the subject changes.
6. If the speaker enumerated things, you may set them out as a list, one item to a line beginning \
with "- ". Otherwise write continuous prose.
7. Change nothing else. Every claim, number, quantity, name, technical term, hedge, and \
qualification the speaker used must appear in your output, saying the same thing it said before. \
Do not summarise, shorten, condense, reorder, interpret, correct, or add. Your output should be \
close to the same length as the input.
8. Leave garbled or nonsensical passages exactly as they are. They are mistranscriptions, and \
guessing at what was meant invents content.
9. Write plain text. No bold, no italics, no headings, no quotation marks wrapped around the whole \
passage, no markup of any kind. Lists as described in 6 are the only structure permitted.
10. Output only the cleaned text — no preamble, no closing remark, no notes about what you changed.
"""

#: Extra request-body fields that ask a provider not to think before answering.
#:
#: There is no portable way to do this. Each of these is understood by some servers and ignored by
#: others: ``reasoning_effort`` by OpenAI and vLLM, ``chat_template_kwargs`` by vLLM and llama.cpp
#: for Qwen-style templates, ``think`` by Ollama. Sending all three works because almost every
#: OpenAI-compatible server ignores fields it does not recognise — and for the few that do not, the
#: worker retries once without them and then stops sending them for the rest of the session.
#:
#: They are belt and braces regardless: the client already keeps reasoning in a separate field from
#: the answer, so working that arrives anyway is discarded rather than shown.
NO_REASONING_EXTRAS: Final[dict[str, Any]] = {
    "reasoning_effort": "none",
    "chat_template_kwargs": {"enable_thinking": False},
    "think": False,
}
