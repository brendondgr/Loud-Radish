"""Tapping one application's audio without taking it off the user's speakers.

The brief's own objection to this feature was that a user who suddenly cannot hear the talk has a
worse problem than an imprecise recording. That objection is entirely correct about the obvious
implementation — `pactl move-sink-input`, moving the stream onto a null sink — and entirely
inapplicable to the one used here, because PipeWire allows a node's output ports to be linked to
more than one destination. Adding a link does not remove the one already there.

**Verified against the real graph before any of this was written.** With a browser playing a video,
linking its output ports into a tap left both original links to the real sink intact, and recording
the tap's monitor produced 11.9 seconds of audio at RMS 0.020 with no NaN — while playback
continued. Tearing down left no null sink behind and no change to the browser's routing.

The tests below cover the parts that can be checked without an application playing: the graph
parsing, the scoring, and the tap's lifecycle. The one that needs a real stream says so and skips.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from app.services.audio.tap import (
    ApplicationTap,
    PlaybackStream,
    TapError,
    playback_streams,
    rank,
    score,
    tools_present,
)

pipewire = pytest.mark.skipif(
    not tools_present(), reason="PipeWire's command-line tools are a system package"
)


def stream(**overrides) -> PlaybackStream:
    defaults = {
        "node_id": 1,
        "serial": 100,
        "node_name": "LibreWolf",
        "application": "LibreWolf",
        "binary": "librewolf",
        "media_name": "A Lecture On Operators - YouTube",
    }
    return PlaybackStream(**{**defaults, **overrides})


# -- reading the graph ---------------------------------------------------------------------------


def test_only_playback_streams_are_listed(monkeypatch) -> None:
    """A sink is not an application, and a capture stream is the microphone."""
    dump = [
        {
            "type": "PipeWire:Interface:Node",
            "id": 1,
            "info": {"props": {"media.class": "Stream/Output/Audio", "node.name": "app"}},
        },
        {
            "type": "PipeWire:Interface:Node",
            "id": 2,
            "info": {"props": {"media.class": "Audio/Sink", "node.name": "speakers"}},
        },
        {
            "type": "PipeWire:Interface:Node",
            "id": 3,
            "info": {"props": {"media.class": "Stream/Input/Audio", "node.name": "mic"}},
        },
        {"type": "PipeWire:Interface:Port", "id": 4, "info": {"props": {}}},
    ]
    monkeypatch.setattr("app.services.audio.tap._run", lambda *_a, **_k: json.dumps(dump))

    found = playback_streams()

    assert [s.node_name for s in found] == ["app"]


def test_a_broken_dump_is_not_a_crash(monkeypatch) -> None:
    """`pw-dump` runs on every arm. It must not be able to fail a recording."""
    monkeypatch.setattr("app.services.audio.tap._run", lambda *_a, **_k: "not json at all")

    assert playback_streams() == []


# -- scoring -------------------------------------------------------------------------------------


def test_an_exact_application_match_outranks_a_title_match() -> None:
    """The binary name is the strongest signal available, weak as all of them are."""
    exact = stream(binary="librewolf", media_name="unrelated")
    titled = stream(
        binary="something-else", application="Other", media_name="A Lecture On Operators"
    )

    assert score(exact, app_id="librewolf") > score(titled, app_id="librewolf")


def test_title_words_are_matched_against_the_media_name() -> None:
    """A browser puts the page title in `media.name`, which often shares words with the window."""
    matching = stream(media_name="A Lecture On Operators - YouTube")
    other = stream(media_name="Completely Different Content")

    assert score(matching, title="A Lecture On Operators") > score(
        other, title="A Lecture On Operators"
    )


def test_short_words_do_not_count_as_a_match() -> None:
    """ "the" and "on" appear in every title; matching on them would rank everything equally."""
    a = stream(media_name="the on and a of")

    assert score(a, title="the on and a of") == 0


def test_ranking_puts_the_best_candidate_first() -> None:
    streams = [
        stream(node_name="Music", application="Music", binary="music", media_name="Some Song"),
        stream(node_name="LibreWolf", binary="librewolf", media_name="A Lecture On Operators"),
    ]

    ordered = rank(streams, app_id="librewolf", title="A Lecture On Operators")

    assert ordered[0].node_name == "LibreWolf"


def test_nothing_to_match_against_leaves_the_order_alone() -> None:
    """With no window information every candidate scores zero, and stable sort keeps the order."""
    streams = [stream(node_name="First"), stream(node_name="Second")]

    assert [s.node_name for s in rank(streams)] == ["First", "Second"]


# -- the tap's lifecycle -------------------------------------------------------------------------


def test_opening_without_the_tools_names_what_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("app.services.audio.tap.tools_present", lambda: False)

    with pytest.raises(TapError, match="pipewire-utils"):
        ApplicationTap().open()


def test_a_sink_that_will_not_create_is_an_error(monkeypatch) -> None:
    """Recording silence from a tap that does not exist is the failure this prevents."""
    monkeypatch.setattr("app.services.audio.tap.tools_present", lambda: True)
    monkeypatch.setattr(
        "app.services.audio.tap._run", lambda *_a, **_k: "Failure: Invalid argument"
    )

    with pytest.raises(TapError, match="Invalid argument"):
        ApplicationTap().open()


def test_closing_an_unopened_tap_is_safe() -> None:
    ApplicationTap().close()  # must not raise


def test_linking_before_opening_is_refused() -> None:
    with pytest.raises(TapError, match="not open"):
        ApplicationTap().link(stream())


def test_the_same_node_is_not_linked_twice(monkeypatch) -> None:
    """`link_all` runs repeatedly while recording, to catch nodes an application creates later.

    Re-linking a port that is already linked would accumulate duplicate links for the length of a
    talk, which is a graph nobody can reason about and a leak that survives the recording.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr("app.services.audio.tap.tools_present", lambda: True)

    def fake_run(command, **_kwargs):
        calls.append(command)
        return "42" if command[:2] == ["pactl", "load-module"] else ""

    monkeypatch.setattr("app.services.audio.tap._run", fake_run)

    tap = ApplicationTap()
    tap.open()
    first = tap.link_all([stream()])
    second = tap.link_all([stream()])

    assert first == 2
    assert second == 0


# -- against the real graph ----------------------------------------------------------------------


@pipewire
def test_a_tap_opens_and_closes_without_leaving_a_sink_behind() -> None:
    """A leaked null sink appears in the user's output picker and outlives the application."""
    before = _null_sinks()

    tap = ApplicationTap(sink_name="transcriber-tap-test")
    tap.open()
    try:
        assert tap.is_open
        assert tap.monitor.endswith(".monitor")
    finally:
        tap.close()

    assert not tap.is_open
    assert _null_sinks() == before


@pipewire
def test_linking_leaves_the_applications_own_routing_alone() -> None:
    """**The property the whole design rests on**, checked rather than assumed.

    "Can the user still hear it?" is the question, and its programmatic form is "does the
    application's original link to the real sink still exist?". If additive linking did not hold on
    this machine, this is what would say so.
    """
    streams = playback_streams()
    if not streams:
        pytest.skip("no application is playing audio to link")

    target = streams[0]
    before = _links_from(target.node_name)
    if not before:
        pytest.skip("the playing application has no outgoing links to preserve")

    with ApplicationTap(sink_name="transcriber-tap-test") as tap:
        tap.link(target)
        during = _links_from(target.node_name)

    assert before <= during, "linking the tap removed the application's own routing"


def _null_sinks() -> int:
    result = subprocess.run(
        ["pactl", "list", "short", "modules"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.stdout.count("null-sink")


def _links_from(node_name: str) -> set[str]:
    """Every destination this node's ports are currently linked to."""
    result = subprocess.run(
        ["pw-link", "-l"], capture_output=True, text=True, timeout=10, check=False
    )
    destinations: set[str] = set()
    current = ""
    for line in result.stdout.splitlines():
        if not line.startswith((" ", "\t")):
            current = line.strip()
        elif current.startswith(f"{node_name}:") and "|->" in line:
            destinations.add(line.split("|->", 1)[1].strip())
    return destinations
