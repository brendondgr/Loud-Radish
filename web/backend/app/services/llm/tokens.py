"""Token estimation for the context budget (BE §9.4).

No tokeniser is shipped. Every provider tokenises differently, the correct tokeniser for a local
model is not knowable from its name, and downloading one per model to count characters would be a
poor trade. What the budget actually needs is a *conservative upper bound*: overestimating costs a
little unused context, whereas underestimating overflows the window and the provider truncates the
prompt silently — from the front, which is exactly where the instructions live.

So the estimate is deliberately pessimistic. Roughly 3.6 characters per token across English prose
sits below the usual 4.0 rule of thumb, and technical text with jargon and numbers tokenises worse
than prose, which is precisely the material this application handles.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Characters per token. Below the usual 4.0 on purpose — see the module docstring.
CHARS_PER_TOKEN = 3.6

#: Per-message overhead for role markers and separators, charged on every message.
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str) -> int:
    """Estimate the tokens in ``text``, rounding up."""
    if not text:
        return 0
    return int(len(text) / CHARS_PER_TOKEN) + 1


def estimate_messages(messages: Iterable[object]) -> int:
    """Estimate the tokens in a list of :class:`~.contract.LlmMessage`.

    Accepts anything with a ``content`` attribute so callers are not forced to import the contract
    just to measure a draft.
    """
    total = 0
    for message in messages:
        content = getattr(message, "content", "") or ""
        total += estimate_tokens(str(content)) + MESSAGE_OVERHEAD_TOKENS
    return total


def fit_to_budget(chunks: Iterable[str], budget_tokens: int) -> tuple[list[str], int]:
    """Take chunks in priority order until the budget is spent.

    Returns the chunks that fit and the tokens they consume. A chunk that does not fit is skipped
    rather than truncated: half a transcript excerpt ending mid-sentence is worse than none, because
    the model treats the fragment as complete and answers from it.

    Iteration stops at the **first** chunk that does not fit rather than continuing to look for a
    smaller one, so priority order is honoured strictly. Reordering the input is the caller's job.
    """
    kept: list[str] = []
    spent = 0
    for chunk in chunks:
        cost = estimate_tokens(chunk)
        if spent + cost > budget_tokens:
            break
        kept.append(chunk)
        spent += cost
    return kept, spent


def trim_to_tokens(text: str, budget_tokens: int) -> str:
    """Cut ``text`` to roughly ``budget_tokens``, from the **front**.

    Used for the recent-verbatim window, where the end of the text is the part being asked about.
    Trimming the front loses the oldest material, which is the correct thing to drop; trimming the
    end would remove the speaker's most recent sentence — the one the question is usually about.

    The cut is moved forward to the next whitespace so the result never begins mid-word.
    """
    if budget_tokens <= 0:
        return ""
    limit = int(budget_tokens * CHARS_PER_TOKEN)
    if len(text) <= limit:
        return text

    cut = len(text) - limit
    space = text.find(" ", cut)
    return text[space + 1 :] if space != -1 else text[cut:]
