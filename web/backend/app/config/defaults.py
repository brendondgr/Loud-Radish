"""Built-in defaults — the bottom configuration layer (BE §13.1).

Values follow the starting points recommended in the architecture document: LocalAgreement-2, a
~0.75 s step, a 1–25 s buffer window, a 15 s commit timeout, and a 500 ms pause threshold. They are
conservative on purpose. The user is expected to re-tune the ASR model and compute device after
benchmarking on their own hardware (BE §21.4).
"""

from __future__ import annotations

from typing import Any

from .schema import AppConfig, QuickAction

DEFAULT_QUICK_ACTIONS: list[QuickAction] = [
    QuickAction(
        id="summarise_10",
        label="Summarise the last 10 minutes",
        hint="⌘1",
        prompt=(
            "Summarise what the speaker covered in the last ten minutes. "
            "Lead with the main claim, then the supporting steps."
        ),
        range_minutes=10.0,
    ),
    QuickAction(
        id="main_argument",
        label="Main argument so far",
        hint="⌘2",
        prompt=(
            "What is the speaker's central argument so far? "
            "State it in two or three sentences, then list what it rests on."
        ),
    ),
    QuickAction(
        id="define_terms",
        label="Define recent terms",
        hint="⌘3",
        prompt=(
            "Define the technical terms, acronyms, and named methods used recently, "
            "each in one plain-language line for someone new to the field."
        ),
        range_minutes=6.0,
    ),
    QuickAction(
        id="what_missed",
        label="What did I miss?",
        hint="⌘4",
        prompt=(
            "Summarise what was said since I last read the transcript. "
            "Be concrete about anything that changed the direction of the talk."
        ),
        since_last_read=True,
    ),
    QuickAction(
        id="explain_simply",
        label="Explain the last point simply",
        hint="",
        prompt=(
            "Explain the speaker's most recent point as if I do not know this field. "
            "Avoid jargon, or define it inline where it is unavoidable."
        ),
        range_minutes=3.0,
    ),
]


def default_config() -> AppConfig:
    """Return a fresh configuration built entirely from schema defaults."""
    return AppConfig(quick_actions=list(DEFAULT_QUICK_ACTIONS))


def default_layer() -> dict[str, Any]:
    """Return the defaults as a plain nested dict, for use as the base merge layer."""
    return default_config().model_dump(mode="json")
