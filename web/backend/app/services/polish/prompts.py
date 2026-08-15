"""The polish instruction list.

Written as a numbered list of operations rather than as a request, because the two produce
different behaviour. "Please tidy this transcript" is an invitation to improve it, and a model
offered that invitation rewrites for concision — which here means deleting things the speaker
said. A numbered list of permitted edits, with an explicit statement of what must not change, is
the difference between a cleaned transcript and a summary wearing one's clothes.

The goal is a *clean, accurate dialogue* rather than a literal transcription. Three instructions
carry the weight, and each of them is here because of a specific way the output was wrong:

* **Spoken forms (2 and 3).** A speaker saying "guard dot py" is naming a file, and rendering that
  literally puts *guard dot py* on the page where ``guard.py`` belongs. The correction changes the
  form of a reference, not the reference — and because a written form is a different number of
  words from a spoken one, the wording immediately around it has to be allowed to change with it or
  the sentence stops parsing.
* **One paragraph (7).** A transcript that breaks to a new line every few seconds is disruptive to
  read. The whole chunk is handed over as one continuous run and one continuous run is what should
  come back. :func:`..guard.collapse_to_paragraph` enforces this when the model does not.
* **Integrity (8).** The guarantee that the reader is still reading what was actually said. It has
  to be stated *after* the licence granted in 2 and 3, and phrased to bound it, or a model reads
  "correct what the speaker said" as permission it was never given.

The timestamps in 5 are not the model's to compute. They arrive already placed by
:mod:`.source` and are carried along; :func:`..guard.reconcile_timestamps` removes any the model
invents anyway.
"""

from __future__ import annotations

from typing import Any, Final

POLISH_PROMPT: Final = """\
You are turning a live speech-to-text transcript into readable dialogue. You will be given one \
stretch of a talk as a single unbroken run of text. Rewrite the whole run and return it. Apply the \
following instructions directly. Do not reason about the task first, do not plan, and do not \
explain yourself — write the finished text and nothing else.

1. Remove filler sounds and hesitations ("um", "uh", "er", "ah", "mm"), false starts where the \
speaker abandoned a phrase and restarted, and words duplicated where the speaker plainly said them \
once. These are speech-model artefacts, not speech.
2. Write out anything the speaker dictated aloud in its proper written form. Spoken punctuation \
inside a name becomes real punctuation: "guard dot py" is guard.py, "numpy dot array" is \
numpy.array, "slash api slash health" is /api/health, "example dot com" is example.com. \
Spelled-out acronyms become acronyms: "H T T P" is HTTP. Spoken casing conventions are applied: \
"camel case get user" is getUser, "snake case max retries" is max_retries. Use exactly the name \
the speaker said — do not correct their spelling, expand an abbreviation they used, or substitute \
a name you think they meant.
3. Adjust the wording immediately around such a correction so the sentence still reads correctly. \
A written form is a different number of words from a spoken one, and articles, verb agreement, and \
connecting words have to follow it. This licence covers those words and nothing further.
4. Add correct punctuation and capitalisation, and break the run-on text into complete sentences \
at the points the speaker's phrasing indicates.
5. Keep every [MM:SS] timestamp. Each one marks the moment the words after it were spoken, so keep \
it immediately before that same material wherever that material now sits. Do not add timestamps, \
do not remove them, do not change their numbers, and do not move them past one another.
6. If the speaker enumerated things, you may set them out as a list, one item to a line beginning \
with "- ". This is the only case where a line break is allowed.
7. Otherwise write the whole passage as ONE continuous paragraph. Do not start new paragraphs, do \
not insert blank lines, and do not break the text into lines. It is meant to read as flowing \
prose.
8. Change nothing else. Every claim, number, quantity, name, technical term, hedge, and \
qualification the speaker used must appear in your output, saying the same thing it said before. \
Do not summarise, shorten, condense, reorder, interpret, or add. Apart from the corrections \
described in 2 and 3, your output should be close to the same length as the input.
9. Leave garbled or nonsensical passages exactly as they are. They are mistranscriptions, and \
guessing at what was meant invents content. Writing out a name the speaker dictated is not \
guessing; repairing a phrase you cannot make sense of is.
10. Write plain text. No bold, no italics, no headings, no backticks, no quotation marks wrapped \
around the whole passage, no markup of any kind.
11. Output only the finished text — no preamble, no closing remark, no notes about what you \
changed.
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
