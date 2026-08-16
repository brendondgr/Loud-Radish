"""The prompts (BE §9.6, §11.2).

Kept in one module because they are the part of this system most likely to need adjusting, and
hunting for prompt fragments spread across an orchestrator is miserable work.

Three instructions do the heavy lifting, and each is here because of a specific failure:

* **Answer only from the transcript.** A language model asked about a physics seminar will happily
  answer from its own knowledge of physics, producing something plausible that the speaker never
  said. That is worse than no answer, because it is indistinguishable from a correct one.
* **The transcript is machine-generated.** Names, acronyms, and jargon are frequently wrong. A model
  told this reads through an obvious mistranscription; a model not told it treats "Hilbert space"
  rendered as "hill bear space" as a term it should reason about.
* **Say when it is not there.** Without this the model fills the gap rather than reporting it.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are helping someone follow a talk while it is being given. They are listening and cannot \
re-read what was said, so answer briefly and directly.

Everything you know about this talk comes from the transcript below. Answer from it and from \
nothing else. If the transcript does not contain the answer, say so plainly — "the speaker has not \
covered that yet" is a useful answer; an invented one is not.

The transcript is produced by a speech model in real time and contains errors. Proper nouns, \
acronyms, and technical terms are the least reliable parts of it. Read through obvious \
mistranscriptions rather than treating them as terminology, and do not quote a garbled phrase back \
as if it were what the speaker said.

When you refer to a specific moment, cite it as [MM:SS] — and **copy the timestamp exactly as it \
appears at the start of a transcript line**. Do not calculate one, do not estimate one, and do not \
give a timestamp that is not written below. If a passage you want to refer to has no timestamp of \
its own, describe when it happened in words instead of inventing a number. Cite the moment the \
point was made, not the moment it was mentioned again.

The summary section carries ranges rather than moments, and its text is compressed rather than \
spoken. Never cite a timestamp from it — find the verbatim line instead, or say when it happened \
in words.

Write in prose, not bullet points, unless the question asks for a list. Do not restate the \
question. Do not open with a preamble about what you are about to do."""

#: Prefixes each block of context so the model can tell verbatim speech from a summary of it. A
#: model given both without labels treats a compressed summary as something the speaker said word
#: for word, and then quotes it.
SECTION_HEADERS = {
    "recent": "## What the speaker has just been saying (verbatim)",
    "retrieved": "## Earlier passages that may be relevant (verbatim)",
    "summaries": "## Summary of the talk so far (compressed — not the speaker's words)",
    "glossary": "## Terms introduced in this talk",
    "quote": "## The passage the user has selected",
    "metadata": "## About this talk",
}

SUMMARY_PROMPT = """\
Summarise this stretch of a talk in three or four sentences.

Lead with the claim being made. Then the evidence or argument for it. Keep any technical term the \
speaker introduced, and keep numbers exactly. Omit hedging, repetition, and asides.

Write only the summary. No preamble, no heading, no bullet points."""

GLOSSARY_PROMPT = """\
List the technical terms, acronyms, and named methods this passage introduces, and define each one \
*as this speaker used it* — not as a textbook would.

Only include terms a competent person outside this subfield would not already know. Do not include \
ordinary words, names of people, or terms you are guessing at.

One per line, formatted exactly as:

TERM :: definition in one sentence

If the passage introduces no such terms, reply with nothing at all."""


def quick_action_prompt(prompt: str, since_last_read: bool = False) -> str:
    """Adjust a quick action's prompt for the context it will be given.

    The "what did I miss" action is the only one whose meaning depends on the window it receives, so
    it is told the window is already the right one — otherwise the model tries to work out for
    itself which part is new and guesses.
    """
    if not since_last_read:
        return prompt
    return (
        f"{prompt}\n\n"
        "The transcript you have been given is exactly the stretch the user has not read. "
        "Cover all of it; do not try to work out where their attention lapsed."
    )


def selection_prompt(quote: str, question: str) -> str:
    """Compose the "ask about this" question from a transcript selection."""
    asked = question.strip() or "What does this mean?"
    return f'The user selected this from the transcript:\n\n"{quote.strip()}"\n\n{asked}'
