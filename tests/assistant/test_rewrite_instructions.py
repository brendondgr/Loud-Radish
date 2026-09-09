"""Blank means the instructions that shipped (D-068).

The rule is one line in each prompts module and it is the load-bearing part of the whole feature,
because it is what separates two very different products:

* **A blank field meaning the shipped text.** An installation that never edits the instructions
  keeps tracking whatever ships next, and only a list somebody actually wrote is held on disk.
* **A field seeded with the shipped text.** Every installation freezes on the wording current at
  the moment somebody first opened the settings dialog, whether or not they changed anything.

The second is the easy mistake, in the schema, in the panel, and in a Reset button that writes the
shipped text back rather than writing nothing. These pin the first.
"""

from __future__ import annotations

import pytest
from app.config.defaults import default_config
from app.services.dictation import prompts as dictation
from app.services.polish import prompts as polish

PASSES = [
    pytest.param(polish, "polish", polish.DEFAULT_POLISH_PROMPT, id="polish"),
    pytest.param(dictation, "dictation", dictation.DEFAULT_DICTATION_PROMPT, id="dictation"),
]


@pytest.mark.parametrize(("module", "area", "shipped"), PASSES)
def test_an_untouched_installation_gets_the_shipped_text(module, area, shipped) -> None:
    assert module.resolve(default_config()) == shipped


@pytest.mark.parametrize(("module", "area", "shipped"), PASSES)
def test_nothing_is_stored_until_somebody_writes_something(module, area, shipped) -> None:
    """The default is empty rather than a copy of the text, which is what lets a later release's
    wording reach a user who never touched the field."""
    assert getattr(default_config(), area).instructions == ""


@pytest.mark.parametrize(("module", "area", "shipped"), PASSES)
def test_a_written_list_replaces_the_shipped_one_entirely(module, area, shipped) -> None:
    config = default_config()
    getattr(config, area).instructions = "Do it my way."

    assert module.resolve(config) == "Do it my way."


@pytest.mark.parametrize(("module", "area", "shipped"), PASSES)
def test_a_cleared_field_falls_back_rather_than_asking_for_nothing(module, area, shipped) -> None:
    """Selecting all and deleting leaves a newline behind. Sending that as the system message
    would hand the model a page of transcript with nothing asked of it."""
    config = default_config()
    getattr(config, area).instructions = "   \n\t  "

    assert module.resolve(config) == shipped


@pytest.mark.parametrize(("module", "area", "shipped"), PASSES)
def test_surrounding_whitespace_is_trimmed_from_a_real_list(module, area, shipped) -> None:
    config = default_config()
    getattr(config, area).instructions = "\n  Punctuation only.  \n"

    assert module.resolve(config) == "Punctuation only."


def test_the_two_passes_ship_different_instructions() -> None:
    """Dictation is narrower on purpose: the words are the user's own, and a model that improves
    them has changed what they said into what it would have said."""
    assert polish.DEFAULT_POLISH_PROMPT != dictation.DEFAULT_DICTATION_PROMPT


@pytest.mark.parametrize(("module", "area", "shipped"), PASSES)
def test_a_written_list_for_one_pass_leaves_the_other_alone(module, area, shipped) -> None:
    """They are separate settings. Editing the transcript clean-up must not change what gets
    pasted into somebody's document."""
    config = default_config()
    getattr(config, area).instructions = "Do it my way."
    other = dictation if module is polish else polish

    assert other.resolve(config) == (
        dictation.DEFAULT_DICTATION_PROMPT if module is polish else polish.DEFAULT_POLISH_PROMPT
    )
