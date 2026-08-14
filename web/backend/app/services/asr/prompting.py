"""Term biasing (BE §6.5).

Domain vocabulary is where transcription fails in exactly the situation this application exists for.
Proper nouns and technical terms the model has never encountered get mangled into phonetically
similar common words — "Kullback-Leibler" becomes "cool back libeler" — and the resulting
transcript is worse than useless for the one question the user wanted to ask.

Two mechanisms, both optional and individually toggleable, because a badly chosen prompt can
*induce* hallucination and you will want to A/B them:

* **Session prompt** — the abstract, title, or expected terminology, pasted before the talk. Cheap
  and surprisingly effective.
* **Rolling context** — the last sentence or two of committed text, fed back as prior context. This
  improves continuity across buffer boundaries and helps the model keep spelling a term the same way
  once it has seen it.

The glossary from the context pipeline also feeds in here (BE §9.3): once a term has been recognised
correctly and defined, biasing on it makes the ASR more likely to keep getting it right.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...config.schema import AsrConfig

#: Prompts longer than this crowd out the audio's own context and start to induce hallucination.
#: Whisper's prompt window is 224 tokens; this is a conservative character budget for that.
MAX_PROMPT_CHARS = 800

#: Separator between the parts of a composed prompt.
JOIN = " "


@dataclass
class PromptBuilder:
    """Composes the biasing prompt for each inference pass."""

    config: AsrConfig
    #: Terms accumulated by the context pipeline, most recently added last.
    glossary_terms: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.glossary_terms is None:
            self.glossary_terms = []

    def build(self, recent_committed: str = "") -> str | None:
        """Compose the prompt for one pass.

        Args:
            recent_committed: the tail of already-committed text, for rolling context.

        Returns:
            The prompt, or ``None`` when both mechanisms are disabled or produce nothing — the
            backend then runs unbiased rather than being handed an empty string, which some
            backends treat differently from no prompt at all.
        """
        parts: list[str] = []

        if self.config.use_session_prompt and self.config.session_prompt.strip():
            parts.append(self.config.session_prompt.strip())

        if self.config.use_session_prompt and self.glossary_terms:
            parts.append(_glossary_clause(self.glossary_terms))

        if self.config.use_rolling_prompt and recent_committed.strip():
            parts.append(_tail(recent_committed, self.config.rolling_prompt_chars))

        if not parts:
            return None

        # Trim from the front, keeping the rolling context: recent words are the strongest signal
        # for what comes next, while the session prompt is background.
        prompt = JOIN.join(parts)
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[-MAX_PROMPT_CHARS:].lstrip()
        return prompt or None

    def add_glossary_terms(self, terms: list[str]) -> None:
        """Feed newly-identified terms back into biasing, ignoring duplicates."""
        known = set(self.glossary_terms)
        for term in terms:
            cleaned = term.strip()
            if cleaned and cleaned not in known:
                self.glossary_terms.append(cleaned)
                known.add(cleaned)

    def update_config(self, config: AsrConfig) -> None:
        """Adopt new settings. Both toggles and the session prompt are live settings."""
        self.config = config


def _glossary_clause(terms: list[str]) -> str:
    """Render glossary terms as a biasing clause.

    Most recent terms first, and only as many as fit: a term that has just appeared is the one most
    likely to appear again in the next few seconds.
    """
    selected: list[str] = []
    budget = MAX_PROMPT_CHARS // 2
    for term in reversed(terms):
        if sum(len(t) + 2 for t in selected) + len(term) > budget:
            break
        selected.append(term)
    return f"Terms used in this talk: {', '.join(selected)}." if selected else ""


def _tail(text: str, max_chars: int) -> str:
    """Return the last ``max_chars`` of ``text``, cut at a word boundary."""
    if max_chars <= 0 or not text:
        return ""
    if len(text) <= max_chars:
        return text.strip()
    clipped = text[-max_chars:]
    space = clipped.find(" ")
    return clipped[space + 1 :].strip() if space != -1 else clipped.strip()
